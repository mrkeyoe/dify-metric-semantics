"""Arbitration: turn candidate definitions into one explainable decision.

The deterministic precheck (``conflict.py``) is always computed; the model is
only asked to judge *semantics*. Model failures never crash the tool — they
degrade to ``requires_human=true`` with an explicit strategy label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import Config
from .conflict import describe_for_prompt, detect_conflicts
from .errors import LLMError, LLMUnavailable
from .llm import LLMClient, describe_client
from .models import Candidate, ConflictReport
from .prompts import (
    ARBITRATION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_arbitration_prompt,
    parse_arbitration_response,
)


@dataclass
class ArbitrationResult:
    resolved: bool
    chosen_id: Optional[str]
    chosen_name: Optional[str]
    rationale: str
    requires_human: bool
    confidence: float
    strategy: str
    llm: str = "none"
    conflict: bool = False
    prompt_version: str = PROMPT_VERSION
    fallback_order: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved": bool(self.resolved),
            "chosen_id": self.chosen_id,
            "chosen_name": self.chosen_name,
            "rationale": self.rationale,
            "requires_human": bool(self.requires_human),
            "confidence": round(float(self.confidence), 6),
            "strategy": self.strategy,
            "llm": self.llm,
            "conflict": bool(self.conflict),
            "prompt_version": self.prompt_version,
            "fallback_order": list(self.fallback_order),
            "signals": dict(self.signals),
            "error": self.error,
        }


def _degraded(
    *,
    strategy: str,
    rationale: str,
    llm_label: str,
    candidates: Sequence[Candidate],
    precheck: ConflictReport,
    threshold: float,
    error: str = "",
) -> ArbitrationResult:
    ranked = sorted(candidates, key=lambda item: (-item.confidence, item.id))
    return ArbitrationResult(
        resolved=False,
        chosen_id=None,
        chosen_name=None,
        rationale=rationale,
        requires_human=True,
        confidence=0.0,
        strategy=strategy,
        llm=llm_label,
        conflict=precheck.conflict,
        fallback_order=[item.id for item in ranked],
        signals={
            "precheck": describe_for_prompt(precheck, candidates),
            "human_threshold": threshold,
            "degraded": True,
        },
        error=error,
    )


def arbitrate_candidates(
    candidates: Sequence[Candidate],
    query: str,
    *,
    llm: Optional[LLMClient] = None,
    config: Optional[Config] = None,
    precheck: Optional[ConflictReport] = None,
    context: str = "",
    confidence_threshold: Optional[float] = None,
) -> ArbitrationResult:
    config = config or Config.from_env()
    threshold = (
        float(confidence_threshold)
        if confidence_threshold is not None
        else float(config.confidence_human_threshold)
    )
    llm_label = describe_client(llm)

    if not candidates:
        return ArbitrationResult(
            resolved=False,
            chosen_id=None,
            chosen_name=None,
            rationale="未匹配到任何已注册口径，无法仲裁；请先注册指标或补充别名。",
            requires_human=True,
            confidence=0.0,
            strategy="no_candidates",
            llm=llm_label,
            signals={"human_threshold": threshold},
        )

    report = precheck or detect_conflicts(
        candidates,
        threshold=config.semantic_distance_threshold,
        numeric_penalty=config.numeric_penalty,
    )
    by_id = {candidate.id: candidate for candidate in candidates}

    if llm is None:
        return _degraded(
            strategy="llm_unavailable",
            rationale=(
                "未配置可用的模型网关，无法做语义等价性判断；请在插件凭据中配置 "
                "model_api_base / model_api_key / model_name（或设置 METRIC_SEMANTICS_LLM_*），"
                "或由人工指定口径。"
            ),
            llm_label=llm_label,
            candidates=candidates,
            precheck=report,
            threshold=threshold,
        )

    prompt = build_arbitration_prompt(
        query,
        candidates,
        describe_for_prompt(report, candidates),
        context=context,
        max_definition_chars=config.max_definition_chars,
    )

    try:
        raw = llm.complete(prompt, system=ARBITRATION_SYSTEM_PROMPT, temperature=0.0, max_tokens=1200)
    except (LLMError, LLMUnavailable) as exc:
        return _degraded(
            strategy="llm_error",
            rationale=f"模型调用失败，已降级为人工仲裁：{exc}",
            llm_label=llm_label,
            candidates=candidates,
            precheck=report,
            threshold=threshold,
            error=str(exc),
        )
    except Exception as exc:  # a bad gateway must not break the tool
        return _degraded(
            strategy="llm_error",
            rationale=f"模型调用异常，已降级为人工仲裁：{type(exc).__name__}: {exc}",
            llm_label=llm_label,
            candidates=candidates,
            precheck=report,
            threshold=threshold,
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        payload = parse_arbitration_response(raw)
    except Exception as exc:  # ArbitrationParseError and friends
        return _degraded(
            strategy="llm_unparsable",
            rationale=f"模型输出无法解析为约定 JSON，已降级为人工仲裁：{exc}",
            llm_label=llm_label,
            candidates=candidates,
            precheck=report,
            threshold=threshold,
            error=str(exc),
        )

    chosen_id: Optional[str] = payload["chosen_id"]
    confidence = float(payload["confidence"])
    requires_human = bool(payload["requires_human"])
    rationale = payload["rationale"]
    strategy = "llm_arbitration"
    notes: List[str] = list(payload["conflicts"])

    if chosen_id is not None and chosen_id not in by_id:
        return _degraded(
            strategy="llm_invalid_choice",
            rationale=(
                f"模型返回了候选之外的 id {chosen_id!r}，判定不可信，已降级为人工仲裁。"
                f"模型原始理由：{rationale}"
            ),
            llm_label=llm_label,
            candidates=candidates,
            precheck=report,
            threshold=threshold,
            error=f"chosen_id not in candidates: {chosen_id}",
        )

    if chosen_id is None:
        if payload["equivalent"] and not report.conflict and len(candidates) >= 1:
            best = max(candidates, key=lambda item: (item.confidence, item.match_score))
            chosen_id = best.id
            strategy = "llm_equivalent_auto"
            rationale = rationale or "模型判定候选口径语义等价，按置信度选择优先口径。"
            requires_human = requires_human or confidence < threshold
        else:
            requires_human = True
            strategy = "llm_no_choice"

    resolved = chosen_id is not None and not requires_human
    if confidence < threshold:
        requires_human = True
        resolved = False
    if payload.get("confidence_missing"):
        requires_human = True
        resolved = False
        notes.append("confidence_missing_from_model")

    if report.conflict and payload["equivalent"]:
        notes.append("precheck_conflict_but_model_judged_equivalent")

    ranked = sorted(candidates, key=lambda item: (-item.confidence, item.id))
    return ArbitrationResult(
        resolved=resolved,
        chosen_id=chosen_id,
        chosen_name=by_id[chosen_id].name if chosen_id else None,
        rationale=rationale,
        requires_human=requires_human,
        confidence=confidence,
        strategy=strategy,
        llm=llm_label,
        conflict=report.conflict,
        fallback_order=[item.id for item in ranked],
        signals={
            "precheck": describe_for_prompt(report, candidates),
            "human_threshold": threshold,
            "equivalent": bool(payload["equivalent"]),
            "model_requires_human": bool(payload["requires_human"]),
            "confidence_missing": bool(payload.get("confidence_missing")),
            "model_conflicts": notes,
            "candidate_count": len(candidates),
        },
        error="",
    )
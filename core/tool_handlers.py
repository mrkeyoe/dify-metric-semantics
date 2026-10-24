"""Tool-facing handlers: pure functions over plain dicts.

Dify tool classes are deliberately thin wrappers around these handlers, which
means the whole tool contract (validation, JSON shape, error payloads) is tested
without the Dify SDK installed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .errors import MetricSemanticsError, ValidationError
from .models import Candidate
from .service import MetricSemanticsService


class ToolInputError(ValidationError):
    """A tool parameter is missing or malformed (a user-fixable error)."""


def _require_str(params: Dict[str, Any], name: str) -> str:
    value = params.get(name)
    if value is None or not str(value).strip():
        raise ToolInputError(f"`{name}` is required")
    return str(value).strip()


def _optional_str(params: Dict[str, Any], name: str) -> str:
    value = params.get(name)
    return "" if value is None else str(value).strip()


def _optional_float(params: Dict[str, Any], name: str) -> Optional[float]:
    value = params.get(name)
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"`{name}` must be a number") from exc


def _optional_int(params: Dict[str, Any], name: str, *, minimum: int, maximum: int) -> Optional[int]:
    raw = _optional_float(params, name)
    if raw is None:
        return None
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ToolInputError(f"`{name}` must be within [{minimum}, {maximum}]")
    return value


def _error(exc: BaseException) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": type(exc).__name__,
        "message": str(exc),
        "retryable": isinstance(exc, ToolInputError),
    }


def _guidance(hint: str) -> str:
    if hint == "arbitrate":
        return (
            "检测到多个口径存在冲突。下一步调用 arbitrate（传入 query 或 candidate_ids），"
            "拿到 chosen_id 与 rationale 后再生成 SQL；本插件不执行 SQL。"
        )
    if hint == "use_candidate":
        return (
            "未检测到口径冲突，可直接使用 candidates[0] 的 sql_fragment 构建查询；"
            "执行完成后请调用 record_outcome 回写成功/失败，用于调整 confidence。"
        )
    return (
        "未匹配到已注册口径。请用 register_metric 注册该指标，或提供更精确的指标名/别名；"
        "suggestions 里是相似度最高的已有口径。"
    )


# --------------------------------------------------------------------------- #
# resolve_metric
# --------------------------------------------------------------------------- #


def handle_resolve(service: MetricSemanticsService, params: Dict[str, Any]) -> Dict[str, Any]:
    query = _require_str(params, "query")
    metric_hint = _optional_str(params, "metric_hint") or None
    top_k = _optional_int(params, "top_k", minimum=1, maximum=20)

    payload = service.resolve_to_dict(query, metric_name=metric_hint, top_k=top_k)
    payload["ok"] = True
    payload["guidance"] = _guidance(payload["resolution_hint"])
    if payload["conflict"]:
        payload["next_action"] = "arbitrate"
    elif payload["matched"]:
        payload["next_action"] = "use_candidate"
    else:
        payload["next_action"] = "register_metric"
    return payload


# --------------------------------------------------------------------------- #
# arbitrate
# --------------------------------------------------------------------------- #


def _candidates_from_json(raw: str) -> List[Candidate]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolInputError(f"`candidates_json` is not valid JSON: {exc}") from exc
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list):
        raise ToolInputError("`candidates_json` must be a JSON array of candidate objects")
    candidates: List[Candidate] = []
    for item in data:
        try:
            candidates.append(Candidate.from_dict(item))
        except ValidationError as exc:
            raise ToolInputError(f"invalid candidate in `candidates_json`: {exc}") from exc
    return candidates


def _parse_id_list(raw: str) -> List[str]:
    parts = [part.strip() for part in raw.replace(";", ",").split(",")]
    return [part for part in parts if part]


def handle_arbitrate(service: MetricSemanticsService, params: Dict[str, Any]) -> Dict[str, Any]:
    query = _optional_str(params, "query")
    context = _optional_str(params, "context")
    threshold = _optional_float(params, "confidence_threshold")
    raw_ids = _optional_str(params, "candidate_ids")
    raw_json = _optional_str(params, "candidates_json")

    candidates: List[Candidate] = []
    source = ""
    precheck = None

    if raw_ids:
        ids = _parse_id_list(raw_ids)
        # An unknown id is a caller bug, not bad user input: let the
        # MetricNotFoundError through so `dispatch` marks it non-retryable.
        candidates = service.candidates_from_ids(ids)
        source = "candidate_ids"
    elif raw_json:
        candidates = _candidates_from_json(raw_json)
        source = "candidates_json"
    elif query:
        resolved = service.resolve(query)
        candidates = resolved.candidates
        precheck = resolved.conflict_report
        source = "query_resolve"

    if not candidates:
        raise ToolInputError(
            "no candidates: provide candidate_ids, candidates_json, or a query that matches registered metrics"
        )

    result = service.arbitrate(
        candidates,
        query=query,
        context=context,
        confidence_threshold=threshold,
        precheck=precheck,
    )
    payload = result.to_dict()
    payload["ok"] = True
    payload["candidate_count"] = len(candidates)
    payload["candidate_source"] = source
    payload["query"] = query
    payload["next_action"] = "record_outcome" if result.resolved else "ask_human"
    return payload


# --------------------------------------------------------------------------- #
# record_outcome
# --------------------------------------------------------------------------- #


_OUTCOME_MAP = {
    "success": True,
    "succeeded": True,
    "ok": True,
    "failure": False,
    "failed": False,
    "error": False,
}


def handle_record_outcome(service: MetricSemanticsService, params: Dict[str, Any]) -> Dict[str, Any]:
    metric_id = _require_str(params, "metric_id")
    outcome = _optional_str(params, "outcome").lower()
    signal = _optional_float(params, "signal")
    alpha = _optional_float(params, "alpha")
    note = _optional_str(params, "note")

    success: Optional[bool]
    if outcome:
        if outcome not in _OUTCOME_MAP:
            raise ToolInputError(f"`outcome` must be one of {sorted(_OUTCOME_MAP)}")
        success = _OUTCOME_MAP[outcome]
    else:
        success = None

    if success is None and signal is None:
        raise ToolInputError("provide `outcome` (success/failure) or a numeric `signal` in [0, 1]")

    feedback = service.record_outcome(
        metric_id,
        success=success,
        signal=signal,
        alpha=alpha,
        note=note,
    )

    payload = feedback.to_dict()
    payload["ok"] = True
    payload["next_action"] = "resolve_metric"
    payload["guidance"] = (
        "confidence 已按 EMA 更新（alpha={alpha}）。下次 resolve_metric 会返回新的置信度，"
        "冲突仲裁时该口径的权重随之变化。".format(alpha=round(feedback.alpha, 3))
    )
    return payload


# --------------------------------------------------------------------------- #
# list_metrics (auxiliary, discovery)
# --------------------------------------------------------------------------- #


def handle_list_metrics(service: MetricSemanticsService, params: Dict[str, Any]) -> Dict[str, Any]:
    needle = _optional_str(params, "query").casefold()
    limit = _optional_int(params, "limit", minimum=1, maximum=100) or 20

    definitions = service.list_metrics()
    if needle:
        definitions = [
            definition
            for definition in definitions
            if needle
            in " ".join(
                [definition.id, definition.name, *definition.aliases, definition.definition_text]
            ).casefold()
        ]
    payload = {
        "ok": True,
        "count": len(definitions),
        "truncated": len(definitions) > limit,
        "metrics": [
            {
                "id": definition.id,
                "name": definition.name,
                "aliases": list(definition.aliases),
                "owner": definition.owner,
                "confidence": round(float(definition.confidence), 6),
                "source_tables": list(definition.source_tables),
                "updated_at": definition.updated_at,
            }
            for definition in definitions[:limit]
        ],
        "health": service.health(),
    }
    return payload


TOOL_HANDLERS = {
    "resolve_metric": handle_resolve,
    "arbitrate": handle_arbitrate,
    "record_outcome": handle_record_outcome,
    "list_metrics": handle_list_metrics,
}


def dispatch(tool_name: str, service: MetricSemanticsService, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run a handler and convert failures into a JSON-safe error payload."""

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        known = ", ".join(sorted(TOOL_HANDLERS))
        return _error(ToolInputError(f"unknown tool: {tool_name}; supported tools: {known}"))
    try:
        return handler(service, params or {})
    except MetricSemanticsError as exc:
        return _error(exc)
    except Exception as exc:  # never leak a traceback into the Dify response
        return _error(exc)


__all__ = [
    "ToolInputError",
    "handle_resolve",
    "handle_arbitrate",
    "handle_record_outcome",
    "handle_list_metrics",
    "dispatch",
    "TOOL_HANDLERS",
]
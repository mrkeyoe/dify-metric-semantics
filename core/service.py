"""High-level service used by every adapter (Dify tools, CLI, tests)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .arbitration import ArbitrationResult, arbitrate_candidates
from .config import Config
from .conflict import detect_conflicts, summarize_report
from .errors import ValidationError
from .feedback import ema_update, signal_from_outcome
from .llm import LLMClient, describe_client
from .matching import filter_by_hint, match_definitions, suggest_definitions
from .prompts import PROMPT_VERSION
from .models import (
    Candidate,
    ConflictReport,
    FeedbackResult,
    MetricDefinition,
    ResolveResult,
    make_metric_id,
)
from .storage import BaseMetricStore, open_store


class MetricSemanticsService:
    """Stateless orchestration over a store plus an optional model client."""

    def __init__(
        self,
        *,
        store: Optional[BaseMetricStore] = None,
        llm: Optional[LLMClient] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.config = config or Config.from_env()
        self.store = store if store is not None else open_store(self.config)
        self.llm = llm

    # -- registry ------------------------------------------------------------- #

    def register_metric(
        self,
        *,
        name: str,
        definition_text: str,
        aliases: Optional[Sequence[str]] = None,
        source_tables: Optional[Sequence[str]] = None,
        sql_fragment: Optional[str] = None,
        owner: str = "",
        confidence: float = 0.5,
        metric_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> MetricDefinition:
        definition = MetricDefinition.create(
            name=name,
            definition_text=definition_text,
            aliases=list(aliases or []),
            source_tables=list(source_tables or []),
            sql_fragment=sql_fragment,
            owner=owner,
            confidence=confidence,
            metric_id=metric_id,
            metadata=dict(metadata or {}),
        )
        stored, _created = self.store.upsert(definition, replace=replace)
        return stored

    def register_definition(self, definition: MetricDefinition, *, replace: bool = False) -> MetricDefinition:
        stored, _created = self.store.upsert(definition, replace=replace)
        return stored

    def list_metrics(self) -> List[MetricDefinition]:
        return self.store.list_metrics()

    def get_metric(self, metric_id: str) -> MetricDefinition:
        return self.store.get(metric_id)

    # -- resolve -------------------------------------------------------------- #

    def resolve(
        self,
        query: str,
        *,
        metric_name: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> ResolveResult:
        query = (query or "").strip()
        if not query:
            raise ValidationError("query must not be empty")

        definitions = self.store.list_metrics()
        candidates = match_definitions(definitions, query)
        notes: List[str] = []

        if metric_name:
            filtered = filter_by_hint(candidates, metric_name)
            if filtered:
                if len(filtered) != len(candidates):
                    notes.append(f"metric_hint_filter:{metric_name}->{len(filtered)}/{len(candidates)}")
                candidates = filtered
            else:
                notes.append(f"metric_hint_no_match:{metric_name}")

        report = detect_conflicts(
            candidates,
            threshold=self.config.semantic_distance_threshold,
            numeric_penalty=self.config.numeric_penalty,
        )
        notes.extend(report.notes)

        limit = top_k if top_k is not None else self.config.top_k
        if len(candidates) > limit:
            notes.append(f"candidates_truncated:{len(candidates)}->{limit}")
            visible = candidates[:limit]
        else:
            visible = candidates

        suggestions = suggest_definitions(definitions, query) if not candidates else []
        if not candidates:
            hint = "register_metric"
        elif report.conflict:
            hint = "arbitrate"
        else:
            hint = "use_candidate"

        return ResolveResult(
            query=query,
            matched=bool(candidates),
            conflict=report.conflict,
            candidates=visible,
            conflict_report=report,
            resolution_hint=hint,
            suggestions=suggestions,
            notes=notes,
        )

    def resolve_to_dict(self, query: str, *, metric_name: Optional[str] = None, top_k: Optional[int] = None) -> Dict[str, Any]:
        result = self.resolve(query, metric_name=metric_name, top_k=top_k)
        payload = result.to_dict()
        payload["summary"] = summarize_report(result.conflict_report) if result.conflict_report else ""
        payload["llm_available"] = self.llm is not None
        return payload

    # -- arbitrate ------------------------------------------------------------ #

    def arbitrate(
        self,
        candidates: Sequence[Candidate],
        *,
        query: str = "",
        context: str = "",
        confidence_threshold: Optional[float] = None,
        precheck: Optional[ConflictReport] = None,
    ) -> ArbitrationResult:
        return arbitrate_candidates(
            list(candidates),
            query,
            llm=self.llm,
            config=self.config,
            precheck=precheck,
            context=context,
            confidence_threshold=confidence_threshold,
        )

    def candidates_from_ids(self, ids: Sequence[str]) -> List[Candidate]:
        candidates: List[Candidate] = []
        for metric_id in ids:
            definition = self.store.get(metric_id)
            candidates.append(
                Candidate.from_definition(
                    definition,
                    matched_alias=metric_id,
                    match_kind="explicit_id",
                    match_score=1.0,
                )
            )
        return candidates

    # -- learning loop -------------------------------------------------------- #

    def record_outcome(
        self,
        metric_id: str,
        *,
        success: Optional[bool] = None,
        signal: Optional[float] = None,
        alpha: Optional[float] = None,
        note: str = "",
        source: str = "agent_feedback",
    ) -> FeedbackResult:
        if success is None and signal is None:
            raise ValidationError("either success or signal is required")
        if signal is None:
            signal = signal_from_outcome(bool(success))
        elif success is None:
            success = float(signal) >= 0.5
        if success is False and signal is not None and float(signal) > 0.5:
            raise ValidationError("success=false contradicts a signal above 0.5")

        definition = self.store.get(metric_id)
        old_confidence = float(definition.confidence)
        alpha_value = float(alpha if alpha is not None else self.config.ema_alpha)
        new_confidence = ema_update(old_confidence, float(signal), alpha_value)
        updated = self.store.apply_feedback(
            metric_id,
            old_confidence=old_confidence,
            new_confidence=new_confidence,
            signal=float(signal),
            alpha=alpha_value,
            success=bool(success),
            note=note,
            source=source,
        )
        return FeedbackResult(
            metric_id=metric_id,
            old_confidence=old_confidence,
            new_confidence=float(updated.confidence),
            signal=float(signal),
            alpha=alpha_value,
            successes=int(updated.stats.get("successes", 0)),
            failures=int(updated.stats.get("failures", 0)),
            note=note,
        )

    # -- diagnostics ---------------------------------------------------------- #

    def health(self) -> Dict[str, Any]:
        store = self.store
        return {
            "store": type(store).__name__,
            "store_volatile": bool(getattr(store, "volatile", False)),
            "store_path": str(getattr(store, "path", "")),
            "store_error": getattr(store, "load_error", ""),
            "metric_count": store.count(),
            "llm": describe_client(self.llm),
            "config": self.config.to_dict(),
            "prompt_version": PROMPT_VERSION,
        }


__all__ = ["MetricSemanticsService", "make_metric_id"]
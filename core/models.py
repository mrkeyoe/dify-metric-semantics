"""Dataclasses shared by the store, the detectors and the Dify adapters.

All of them expose ``to_dict()`` returning JSON-serialisable primitives, because
the values ultimately leave the plugin as Dify JSON messages.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .errors import ValidationError

SCHEMA_VERSION = 1
CONFIDENCE_MIN = 0.01
CONFIDENCE_MAX = 0.99


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"confidence must be a number, got {value!r}") from exc
    if number != number:  # NaN
        raise ValidationError("confidence must not be NaN")
    return round(min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, number)), 6)


def _slug(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value or "").strip().lower()
    folded = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", folded).strip("-")
    return (folded[:48] or "metric")


def make_metric_id(name: str, definition_text: str, hint: Optional[str] = None) -> str:
    """Stable, collision-resistant id: readable slug + content hash.

    Two definitions that share a name but differ in text get distinct ids, which
    is exactly what the conflict workflow needs.
    """

    payload = f"{(name or '').strip()}\x00{(definition_text or '').strip()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(hint or name or 'metric')}-{digest}"


def _as_text(
    value: Any,
    field_name: str,
    *,
    required: bool = True,
    max_len: Optional[int] = None,
) -> str:
    if value is None:
        if required:
            raise ValidationError(f"{field_name} is required")
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if required and not text:
        raise ValidationError(f"{field_name} must not be empty")
    if max_len is not None and len(text) > max_len:
        text = text[:max_len]
    return text


def _as_text_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValidationError(f"{field_name} must be a list of strings")
    result: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValidationError(f"{field_name} must contain only strings")
        text = item.strip()
        if text and text not in result:
            result.append(text)
    return result


@dataclass
class MetricDefinition:
    """One concrete metric definition ("口径")."""

    id: str
    name: str
    definition_text: str
    aliases: List[str] = field(default_factory=list)
    source_tables: List[str] = field(default_factory=list)
    sql_fragment: Optional[str] = None
    owner: str = ""
    confidence: float = 0.5
    created_at: str = ""
    updated_at: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _as_text(self.id, "id", max_len=120)
        self.name = _as_text(self.name, "name", max_len=200)
        self.definition_text = _as_text(self.definition_text, "definition_text", max_len=4000)
        self.aliases = _as_text_list(self.aliases, "aliases")
        self.source_tables = _as_text_list(self.source_tables, "source_tables")
        if self.sql_fragment is not None:
            fragment = _as_text(self.sql_fragment, "sql_fragment", required=False, max_len=8000)
            self.sql_fragment = fragment or None
        self.owner = _as_text(self.owner, "owner", required=False, max_len=120)
        self.confidence = clamp_confidence(self.confidence)
        now = utcnow_iso()
        self.created_at = self.created_at or now
        self.updated_at = self.updated_at or self.created_at
        if not isinstance(self.stats, dict):
            raise ValidationError("stats must be a dict")
        if not isinstance(self.metadata, dict):
            raise ValidationError("metadata must be a dict")
        self.stats.setdefault("successes", 0)
        self.stats.setdefault("failures", 0)

    # -- construction helpers ------------------------------------------------ #

    @classmethod
    def create(
        cls,
        name: str,
        definition_text: str,
        *,
        aliases: Optional[List[str]] = None,
        source_tables: Optional[List[str]] = None,
        sql_fragment: Optional[str] = None,
        owner: str = "",
        confidence: float = 0.5,
        metric_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "MetricDefinition":
        metric_id = metric_id or make_metric_id(name, definition_text, aliases[0] if aliases else None)
        return cls(
            id=metric_id,
            name=name,
            definition_text=definition_text,
            aliases=list(aliases or []),
            source_tables=list(source_tables or []),
            sql_fragment=sql_fragment,
            owner=owner,
            confidence=confidence,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricDefinition":
        if not isinstance(data, dict):
            raise ValidationError("metric definition must be a JSON object")
        known = {
            "id",
            "name",
            "definition_text",
            "aliases",
            "source_tables",
            "sql_fragment",
            "owner",
            "confidence",
            "created_at",
            "updated_at",
            "stats",
            "metadata",
        }
        payload = {key: value for key, value in data.items() if key in known}
        return cls(**payload)

    # -- serialisation ------------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "definition_text": self.definition_text,
            "aliases": list(self.aliases),
            "source_tables": list(self.source_tables),
            "sql_fragment": self.sql_fragment,
            "owner": self.owner,
            "confidence": round(float(self.confidence), 6),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stats": dict(self.stats),
            "metadata": dict(self.metadata),
        }


@dataclass
class Candidate:
    """A definition matched against a user question, plus match provenance."""

    id: str
    name: str
    definition_text: str
    aliases: List[str] = field(default_factory=list)
    source_tables: List[str] = field(default_factory=list)
    sql_fragment: Optional[str] = None
    owner: str = ""
    confidence: float = 0.5
    matched_alias: str = ""
    match_kind: str = ""
    match_score: float = 0.0
    updated_at: str = ""

    @classmethod
    def from_definition(
        cls,
        definition: MetricDefinition,
        *,
        matched_alias: str,
        match_kind: str,
        match_score: float,
    ) -> "Candidate":
        return cls(
            id=definition.id,
            name=definition.name,
            definition_text=definition.definition_text,
            aliases=list(definition.aliases),
            source_tables=list(definition.source_tables),
            sql_fragment=definition.sql_fragment,
            owner=definition.owner,
            confidence=definition.confidence,
            matched_alias=matched_alias,
            match_kind=match_kind,
            match_score=round(float(match_score), 6),
            updated_at=definition.updated_at,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Candidate":
        if not isinstance(data, dict):
            raise ValidationError("candidate must be a JSON object")
        if not data.get("id"):
            raise ValidationError("candidate.id is required")
        if not data.get("definition_text"):
            raise ValidationError("candidate.definition_text is required")
        return cls(
            id=_as_text(data.get("id"), "candidate.id", max_len=120),
            name=_as_text(data.get("name", ""), "candidate.name", required=False, max_len=200),
            definition_text=_as_text(data.get("definition_text"), "candidate.definition_text", max_len=4000),
            aliases=_as_text_list(data.get("aliases"), "candidate.aliases"),
            source_tables=_as_text_list(data.get("source_tables"), "candidate.source_tables"),
            sql_fragment=(
                _as_text(data.get("sql_fragment"), "candidate.sql_fragment", required=False, max_len=8000) or None
            ),
            owner=_as_text(data.get("owner", ""), "candidate.owner", required=False, max_len=120),
            confidence=clamp_confidence(data.get("confidence", 0.5)),
            matched_alias=_as_text(
                data.get("matched_alias", ""), "candidate.matched_alias", required=False, max_len=200
            ),
            match_kind=_as_text(data.get("match_kind", ""), "candidate.match_kind", required=False, max_len=40),
            match_score=float(data.get("match_score", 0.0) or 0.0),
            updated_at=_as_text(data.get("updated_at", ""), "candidate.updated_at", required=False, max_len=40),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "definition_text": self.definition_text,
            "aliases": list(self.aliases),
            "source_tables": list(self.source_tables),
            "sql_fragment": self.sql_fragment,
            "owner": self.owner,
            "confidence": round(float(self.confidence), 6),
            "matched_alias": self.matched_alias,
            "match_kind": self.match_kind,
            "match_score": round(float(self.match_score), 6),
            "updated_at": self.updated_at,
        }


@dataclass
class PairConflict:
    """Why two candidates are (or are not) considered conflicting."""

    left_id: str
    right_id: str
    sql_equivalent: Optional[bool]
    semantic_distance: float
    numeric_markers_differ: bool
    is_conflict: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "sql_equivalent": self.sql_equivalent,
            "semantic_distance": round(float(self.semantic_distance), 6),
            "numeric_markers_differ": self.numeric_markers_differ,
            "is_conflict": self.is_conflict,
            "reasons": list(self.reasons),
        }


@dataclass
class ConflictReport:
    conflict: bool
    reasons: List[str] = field(default_factory=list)
    pairs: List[PairConflict] = field(default_factory=list)
    max_semantic_distance: float = 0.0
    threshold: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict": self.conflict,
            "reasons": list(self.reasons),
            "pairs": [pair.to_dict() for pair in self.pairs],
            "max_semantic_distance": round(float(self.max_semantic_distance), 6),
            "threshold": round(float(self.threshold), 6),
            "notes": list(self.notes),
        }


@dataclass
class Suggestion:
    id: str
    name: str
    similarity: float

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "similarity": round(float(self.similarity), 6)}


@dataclass
class ResolveResult:
    query: str
    matched: bool
    conflict: bool
    candidates: List[Candidate] = field(default_factory=list)
    conflict_report: Optional[ConflictReport] = None
    resolution_hint: str = ""
    suggestions: List[Suggestion] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        report = self.conflict_report
        return {
            "query": self.query,
            "matched": self.matched,
            "conflict": self.conflict,
            "resolution_hint": self.resolution_hint,
            "candidate_count": len(self.candidates),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "conflicts": report.to_dict() if report else None,
            "max_semantic_distance": round(float(report.max_semantic_distance), 6) if report else 0.0,
            "threshold": round(float(report.threshold), 6) if report else 0.0,
            "suggestions": [item.to_dict() for item in self.suggestions],
            "notes": list(self.notes),
        }


@dataclass
class FeedbackResult:
    metric_id: str
    old_confidence: float
    new_confidence: float
    signal: float
    alpha: float
    successes: int
    failures: int
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "old_confidence": round(float(self.old_confidence), 6),
            "new_confidence": round(float(self.new_confidence), 6),
            "delta": round(float(self.new_confidence) - float(self.old_confidence), 6),
            "signal": round(float(self.signal), 6),
            "alpha": round(float(self.alpha), 6),
            "successes": self.successes,
            "failures": self.failures,
            "note": self.note,
        }
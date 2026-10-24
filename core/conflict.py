"""Deterministic conflict detection between candidate metric definitions.

Two candidates conflict when *either* holds:

1. both carry a ``sql_fragment`` and their canonical forms differ, or
2. the semantic distance of their ``definition_text`` exceeds the threshold.

The check is intentionally explainable: every verdict lists the reason strings
that produced it, so the agent (and a human reviewer) can audit the decision.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, List, Sequence

from .models import Candidate, ConflictReport, PairConflict
from .text import numeric_markers, semantic_distance, sql_equivalent


def _pair_conflict(left: Candidate, right: Candidate, *, threshold: float, numeric_penalty: float) -> PairConflict:
    same_sql = sql_equivalent(left.sql_fragment, right.sql_fragment)
    distance = semantic_distance(left.definition_text, right.definition_text, numeric_penalty=numeric_penalty)
    left_markers = numeric_markers(left.definition_text)
    right_markers = numeric_markers(right.definition_text)
    markers_differ = bool(left_markers and right_markers and left_markers != right_markers)

    reasons: List[str] = []
    if same_sql is False:
        reasons.append(f"sql_fragment_mismatch:{left.id}~{right.id}")
    if distance > threshold:
        reasons.append(f"definition_distance:{left.id}~{right.id}={distance:.3f}>{threshold:.3f}")
    if markers_differ:
        reasons.append(f"numeric_scope_mismatch:{left.id}~{right.id}")

    is_conflict = same_sql is False or distance > threshold
    return PairConflict(
        left_id=left.id,
        right_id=right.id,
        sql_equivalent=same_sql,
        semantic_distance=distance,
        numeric_markers_differ=markers_differ,
        is_conflict=is_conflict,
        reasons=reasons,
    )


def detect_conflicts(
    candidates: Sequence[Candidate],
    *,
    threshold: float,
    numeric_penalty: float = 0.25,
) -> ConflictReport:
    """Pairwise conflict check over every candidate (not just the top-k)."""

    pairs: List[PairConflict] = []
    reasons: List[str] = []
    notes: List[str] = []

    for left, right in combinations(candidates, 2):
        pair = _pair_conflict(left, right, threshold=threshold, numeric_penalty=numeric_penalty)
        pairs.append(pair)
        for reason in pair.reasons:
            if reason not in reasons:
                reasons.append(reason)

    for candidate in candidates:
        if not candidate.sql_fragment:
            notes.append(f"sql_fragment_missing:{candidate.id}")

    # Disjoint source tables are informative but not decisive on their own:
    # two teams may legitimately read the same logical metric from different
    # tables with an identical definition. Surfaced as a note only.
    for left, right in combinations(candidates, 2):
        if left.source_tables and right.source_tables and not set(left.source_tables) & set(right.source_tables):
            notes.append(f"source_tables_disjoint:{left.id}~{right.id}")

    max_distance = max((pair.semantic_distance for pair in pairs), default=0.0)
    conflict = any(pair.is_conflict for pair in pairs)

    return ConflictReport(
        conflict=conflict,
        reasons=reasons,
        pairs=pairs,
        max_semantic_distance=round(max_distance, 6),
        threshold=float(threshold),
        notes=notes,
    )


def conflicting_ids(report: ConflictReport) -> List[str]:
    """Ids that participate in at least one conflicting pair."""

    ids: List[str] = []
    for pair in report.pairs:
        if not pair.is_conflict:
            continue
        for metric_id in (pair.left_id, pair.right_id):
            if metric_id not in ids:
                ids.append(metric_id)
    return ids


def summarize_report(report: ConflictReport) -> str:
    """One-line human summary, safe to put into a tool message."""

    if not report.pairs:
        return "only one candidate, no pairwise conflict possible"
    if report.conflict:
        ids = ", ".join(conflicting_ids(report))
        return f"conflict detected across [{ids}]: {'; '.join(report.reasons)}"
    return "candidates look equivalent (no SQL mismatch, distance within threshold)"


def describe_for_prompt(report: ConflictReport, candidates: Iterable[Candidate]) -> dict:
    """JSON-able precheck block injected into the arbitration prompt."""

    return {
        "conflict": report.conflict,
        "reasons": list(report.reasons),
        "notes": list(report.notes),
        "threshold": round(float(report.threshold), 6),
        "pairs": [
            {
                "ids": [pair.left_id, pair.right_id],
                "sql_equivalent": pair.sql_equivalent,
                "semantic_distance": round(float(pair.semantic_distance), 6),
                "is_conflict": pair.is_conflict,
            }
            for pair in report.pairs
        ],
        "candidate_ids": [candidate.id for candidate in candidates],
    }
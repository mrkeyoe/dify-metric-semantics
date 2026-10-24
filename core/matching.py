"""Alias / query matching against registered metric definitions."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from .models import Candidate, MetricDefinition, Suggestion
from .text import normalize_text, semantic_similarity

# When a query is a long natural-language sentence, an alias covering a small
# part of it is a weak match; below this ratio we do not consider it a hit.
MIN_COVERAGE = 0.08


def _match_one(definition: MetricDefinition, query_norm: str) -> Optional[tuple]:
    """Return ``(alias, kind, score)`` for the best match, or None."""

    if not query_norm:
        return None

    name_norm = normalize_text(definition.name)
    best: Optional[tuple] = None

    def consider(alias: str, kind: str, score: float) -> None:
        nonlocal best
        if score <= 0:
            return
        if best is None or score > best[2]:
            best = (alias, kind, score)

    if name_norm:
        if query_norm == name_norm:
            consider(definition.name, "name_exact", 1.0)
        elif name_norm in query_norm:
            consider(definition.name, "name_substring", len(name_norm) / max(len(query_norm), 1))

    for alias in definition.aliases:
        alias_norm = normalize_text(alias)
        if not alias_norm:
            continue
        if query_norm == alias_norm:
            consider(alias, "alias_exact", 1.0)
        elif alias_norm in query_norm:
            coverage = len(alias_norm) / max(len(query_norm), 1)
            if coverage >= MIN_COVERAGE:
                consider(alias, "alias_substring", coverage)

    return best


def match_definitions(
    definitions: Iterable[MetricDefinition],
    query: str,
    *,
    limit: Optional[int] = None,
) -> List[Candidate]:
    """All definitions matching ``query``, best first."""

    query_norm = normalize_text(query)
    candidates: List[Candidate] = []
    for definition in definitions:
        hit = _match_one(definition, query_norm)
        if hit is None:
            continue
        alias, kind, score = hit
        candidates.append(
            Candidate.from_definition(
                definition,
                matched_alias=alias,
                match_kind=kind,
                match_score=score,
            )
        )

    candidates.sort(key=lambda item: (-item.match_score, -item.confidence, item.id))
    if limit is not None:
        return candidates[:limit]
    return candidates


def filter_by_hint(candidates: Sequence[Candidate], hint: str) -> List[Candidate]:
    """Keep candidates whose name/aliases overlap an explicit metric hint."""

    hint_norm = normalize_text(hint)
    if not hint_norm:
        return list(candidates)
    kept: List[Candidate] = []
    for candidate in candidates:
        names = [candidate.name, *candidate.aliases]
        if any(hint_norm in normalize_text(name) or normalize_text(name) in hint_norm for name in names if name):
            kept.append(candidate)
    return kept


def suggest_definitions(
    definitions: Iterable[MetricDefinition],
    query: str,
    *,
    limit: int = 3,
    min_similarity: float = 0.3,
) -> List[Suggestion]:
    """Fuzzy near-misses, used when nothing matched exactly."""

    scored = []
    for definition in definitions:
        reference = " ".join([definition.name, *definition.aliases, definition.definition_text])
        similarity = semantic_similarity(query, reference)
        if similarity >= min_similarity:
            scored.append(Suggestion(id=definition.id, name=definition.name, similarity=similarity))
    scored.sort(key=lambda item: (-item.similarity, item.id))
    return scored[:limit]
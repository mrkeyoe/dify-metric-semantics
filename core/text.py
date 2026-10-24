"""Text utilities: normalisation, n-gram similarity, numeric-scope extraction.

Everything here is pure standard library and has no dependency on the Dify SDK,
which is what makes the conflict-detection layer independently testable.
"""

from __future__ import annotations

import re
import unicodedata
from typing import FrozenSet, Iterable, List, Optional, Set, Tuple

# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

_CJK = r"\u4e00-\u9fff"
_KEEP_RE = re.compile(r"[^0-9a-z" + _CJK + r"]+")
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_PUNCT_RE = re.compile(r"\s*([(),=<>!+\-*/;%|])\s*")
_WS_RE = re.compile(r"\s+")

# Longest alternatives first so that "days" wins over "d" and "个月" over "月".
_UNIT_ALIASES: List[Tuple[str, str]] = [
    ("个月", "mo"),
    ("分钟", "min"),
    ("小时", "h"),
    ("星期", "w"),
    ("years", "y"),
    ("year", "y"),
    ("months", "mo"),
    ("month", "mo"),
    ("weeks", "w"),
    ("week", "w"),
    ("days", "d"),
    ("day", "d"),
    ("hours", "h"),
    ("hour", "h"),
    ("minutes", "min"),
    ("minute", "min"),
    ("seconds", "s"),
    ("second", "s"),
    ("天", "d"),
    ("日", "d"),
    ("周", "w"),
    ("月", "mo"),
    ("年", "y"),
    ("秒", "s"),
    ("min", "min"),
    ("%", "pct"),
    ("d", "d"),
    ("w", "w"),
    ("y", "y"),
    ("h", "h"),
    ("s", "s"),
]
_UNIT_PATTERN = "|".join(re.escape(unit) for unit, _ in _UNIT_ALIASES)
_NUMBER_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(" + _UNIT_PATTERN + r")", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """Case-fold, NFKC-normalise and drop everything that is not a word char.

    Chinese text has no spaces, so removal of separators is what lets
    ``"上个月活跃用户数"`` contain the alias ``"活跃用户"``.
    """

    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    return _KEEP_RE.sub("", folded)


def canonical_sql(sql: Optional[str]) -> Optional[str]:
    """Canonical form used for *syntactic* SQL equivalence checks.

    This is deliberately conservative: it only removes comments, redundant
    whitespace, casing and spacing around punctuation. It never rewrites
    semantics, so ``COUNT(DISTINCT a)`` and ``COUNT(DISTINCT b)`` stay unequal.
    """

    if sql is None:
        return None
    text = _SQL_COMMENT_RE.sub(" ", str(sql))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _WS_RE.sub(" ", text).strip()
    text = _SQL_PUNCT_RE.sub(r"\1", text)
    return text.rstrip(";").strip()


def sql_equivalent(left: Optional[str], right: Optional[str]) -> Optional[bool]:
    """Return True/False, or ``None`` when the comparison is not decidable.

    ``None`` means "at least one side has no SQL fragment", in which case the
    caller must fall back to definition-text similarity instead of guessing.
    """

    canonical_left = canonical_sql(left)
    canonical_right = canonical_sql(right)
    if canonical_left is None or canonical_right is None:
        return None
    return canonical_left == canonical_right


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #


def char_ngrams(text: str, n: int = 2) -> FrozenSet[str]:
    """Character n-grams; works for Chinese without a tokenizer."""

    normalized = normalize_text(text)
    if not normalized:
        return frozenset()
    if len(normalized) < n:
        return frozenset({normalized})
    return frozenset(normalized[i : i + n] for i in range(len(normalized) - n + 1))


def token_set(text: str) -> FrozenSet[str]:
    """Latin/number words (length >= 2) plus CJK bigrams."""

    normalized = normalize_text(text)
    tokens: Set[str] = set()
    for word in re.findall(r"[0-9a-z]{2,}", normalized):
        tokens.add(word)
    for gram in char_ngrams(normalized, 2):
        tokens.add(gram)
    return frozenset(tokens)


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set and not right_set:
        return 0.0
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def semantic_similarity(left: str, right: str) -> float:
    """Blend of character-bigram and token Jaccard, both language agnostic."""

    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    bigram_score = jaccard(char_ngrams(left_norm), char_ngrams(right_norm))
    token_score = jaccard(token_set(left_norm), token_set(right_norm))
    return round(0.6 * bigram_score + 0.4 * token_score, 6)


def numeric_markers(text: str) -> FrozenSet[str]:
    """Extract time-window / ratio markers such as ``7d``, ``30mo``, ``5pct``.

    Metric definitions usually disagree on exactly these parameters, so a
    mismatch is a strong (and explainable) non-equivalence signal.
    """

    markers: Set[str] = set()
    for number, unit in _NUMBER_UNIT_RE.findall(text or ""):
        canonical = unit.casefold()
        normalised_unit = dict(_UNIT_ALIASES).get(canonical, canonical)
        number_text = number.rstrip("0").rstrip(".") if "." in number else number
        markers.add(f"{number_text}{normalised_unit}")
    return frozenset(markers)


def semantic_distance(
    left: str,
    right: str,
    *,
    numeric_penalty: float = 0.25,
) -> float:
    """Distance in ``[0, 1]``; 0 means "same wording", 1 means "unrelated"."""

    distance = 1.0 - semantic_similarity(left, right)
    left_markers, right_markers = numeric_markers(left), numeric_markers(right)
    if left_markers and right_markers and left_markers != right_markers:
        distance += numeric_penalty
    elif bool(left_markers) != bool(right_markers):
        distance += numeric_penalty / 2.0
    return round(min(1.0, max(0.0, distance)), 6)


def truncate(text: str, limit: int) -> str:
    """Truncate without breaking surrogate pairs; adds an ellipsis marker."""

    if limit <= 0 or len(text or "") <= limit:
        return text or ""
    return (text or "")[: max(0, limit - 1)].rstrip() + "…"
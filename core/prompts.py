"""Structured prompt for semantic-equivalence arbitration plus a tolerant parser.

The model is asked for a strict JSON object. Parsing is defensive on purpose:
real models wrap JSON in ``` fences, prepend prose or drop optional fields, and
the plugin must degrade to ``requires_human`` instead of crashing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .errors import ArbitrationParseError, ValidationError
from .models import Candidate
from .text import truncate

PROMPT_VERSION = "arbitrate-v1"
MAX_RATIONALE_CHARS = 400

ARBITRATION_SYSTEM_PROMPT = """你是企业数据治理中的「指标口径仲裁器」。你只做一件事：判断多个候选指标口径是否语义等价，并在给定用户问题下选出最合适的一个口径。

判定规则：
1. 等价 = 统计对象相同 + 时间窗口相同 + 过滤条件相同 + 去重/聚合方式相同。任意一项不同即不等价。
2. 必须显式比较最关键的差异来源：时间窗口（如 7 天 vs 30 天）、统计实体（如登录用户 vs 下单用户）、过滤条件（如是否仅统计支付成功）、去重键（user_id vs buyer_id）。
3. 如果只有字面措辞差异、统计语义一致，判定 equivalent=true，chosen_id 取 confidence 较高候选的 id。
4. 如果用户问题提供的信息不足以决定该用哪个口径，判定 equivalent=false、chosen_id=null、requires_human=true，并在 rationale 中说明还缺哪些信息。
5. chosen_id 必须来自给定候选的 id，禁止编造、禁止返回候选之外的值。
6. confidence 是你对本次判断的置信度（0~1 的小数）。口径之间存在硬冲突、或参考信息不足时，必须给出低于 0.6 的置信度。
7. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释性前缀或后缀。

输出 JSON 结构（字段必须齐全）：
{
  "equivalent": <bool>,
  "chosen_id": <候选 id 字符串，或 null>,
  "rationale": <字符串，<=%d 字符，用与用户问题相同的语言，必须点明决定性差异>,
  "confidence": <0~1 的小数>,
  "requires_human": <bool>,
  "conflicts": [{"ids": [<id>, <id>], "type": "semantic|sql|numeric_scope|filter", "detail": <字符串>}]
}""" % MAX_RATIONALE_CHARS


def _candidate_payload(candidate: Candidate, max_definition_chars: int) -> Dict[str, Any]:
    return {
        "id": candidate.id,
        "name": candidate.name,
        "aliases": list(candidate.aliases),
        "definition_text": truncate(candidate.definition_text, max_definition_chars),
        "source_tables": list(candidate.source_tables),
        "sql_fragment": candidate.sql_fragment,
        "owner": candidate.owner,
        "confidence": round(float(candidate.confidence), 6),
        "matched_alias": candidate.matched_alias,
        "match_kind": candidate.match_kind,
    }


def build_arbitration_prompt(
    query: str,
    candidates: Sequence[Candidate],
    precheck: Optional[Dict[str, Any]] = None,
    *,
    context: str = "",
    max_definition_chars: int = 600,
) -> str:
    """Render the user message. Deterministic ordering keeps prompts diffable."""

    payload = {
        "user_query": query,
        "candidates": [_candidate_payload(candidate, max_definition_chars) for candidate in candidates],
        "precheck_signals": precheck or {},
        "extra_context": truncate(context or "", 1200),
    }
    return (
        "请仲裁下列候选口径。\n"
        "输入数据（JSON，其中 precheck_signals 是确定性规则算出的辅助信号，仅供参考，你仍需自行判断语义）：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n只输出约定的 JSON 对象。"
    )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _iter_json_objects(text: str) -> List[str]:
    """All balanced ``{...}`` substrings, ignoring braces inside string literals."""

    objects: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objects.append(text[start : index + 1])
                    start = -1
    return objects


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "是"):
            return True
        if lowered in ("false", "no", "0", "否"):
            return False
    raise ArbitrationParseError(f"{field_name} must be a boolean, got {value!r}")


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise ArbitrationParseError(f"confidence must be a number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ArbitrationParseError(f"confidence must be a number, got {value!r}") from exc
    if number != number:
        raise ArbitrationParseError("confidence must not be NaN")
    if number > 1.0 and number <= 100.0:  # models sometimes answer "85"
        number = number / 100.0
    return round(min(1.0, max(0.0, number)), 6)


def _coerce_conflicts(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ArbitrationParseError("conflicts must be a list")
    conflicts: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        ids = item.get("ids")
        if not isinstance(ids, list):
            ids = []
        conflicts.append(
            {
                "ids": [str(entry) for entry in ids],
                "type": str(item.get("type", "semantic")),
                "detail": truncate(str(item.get("detail", "")), 300),
            }
        )
    return conflicts


def validate_arbitration_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ArbitrationParseError("arbitration answer must be a JSON object")
    if "rationale" not in data:
        raise ArbitrationParseError("missing required field: rationale")
    rationale = str(data.get("rationale") or "").strip()
    if not rationale:
        raise ArbitrationParseError("rationale must not be empty")

    chosen_raw = data.get("chosen_id", data.get("chosen"))
    chosen_id: Optional[str]
    if chosen_raw in (None, "", "null", "None"):
        chosen_id = None
    elif isinstance(chosen_raw, str):
        chosen_id = chosen_raw.strip() or None
    else:
        raise ArbitrationParseError(f"chosen_id must be a string or null, got {type(chosen_raw).__name__}")

    equivalent = _coerce_bool(data.get("equivalent", False), "equivalent")
    confidence_missing = "confidence" not in data or data.get("confidence") is None
    confidence = 0.0 if confidence_missing else _coerce_confidence(data.get("confidence"))
    requires_human = _coerce_bool(data.get("requires_human", False), "requires_human")

    return {
        "equivalent": equivalent,
        "chosen_id": chosen_id,
        "rationale": truncate(rationale, MAX_RATIONALE_CHARS),
        "confidence": confidence,
        "confidence_missing": confidence_missing,
        "requires_human": requires_human,
        "conflicts": _coerce_conflicts(data.get("conflicts")),
    }


def parse_arbitration_response(raw: str) -> Dict[str, Any]:
    """Parse a model answer into the arbitration contract.

    Tries: raw JSON → fenced JSON → the first parseable balanced object.
    Raises ``ArbitrationParseError`` when nothing usable is found.
    """

    if raw is None:
        raise ArbitrationParseError("model returned no content")
    text = str(raw).strip()
    if not text:
        raise ArbitrationParseError("model returned empty content")

    candidates_to_try: List[str] = []
    candidates_to_try.append(text)
    unfenced = _strip_code_fence(text)
    if unfenced != text:
        candidates_to_try.append(unfenced)
    candidates_to_try.extend(_iter_json_objects(unfenced or text))

    errors: List[str] = []
    for blob in candidates_to_try:
        if not blob.strip():
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        try:
            return validate_arbitration_payload(data)
        except (ArbitrationParseError, ValidationError) as exc:
            errors.append(str(exc))

    detail = errors[0] if errors else "no JSON object found"
    raise ArbitrationParseError(
        f"cannot parse arbitration answer ({detail}); raw excerpt: {truncate(text, 240)!r}"
    )
"""Tests for the structured arbitration prompt and its tolerant parser."""

from __future__ import annotations

import json
import unittest

from core.errors import ArbitrationParseError
from core.models import Candidate, MetricDefinition
from core.prompts import (
    ARBITRATION_SYSTEM_PROMPT,
    MAX_RATIONALE_CHARS,
    build_arbitration_prompt,
    parse_arbitration_response,
    validate_arbitration_payload,
)


def candidate() -> Candidate:
    definition = MetricDefinition.create(
        name="活跃用户",
        definition_text="过去 7 天内至少登录过一次的去重用户数（按 user_id 去重，只统计登录行为）",
        metric_id="active_user.7d_login",
        aliases=["日活"],
        source_tables=["dwd.user_login_log"],
        sql_fragment="SELECT COUNT(DISTINCT user_id) FROM dwd.user_login_log",
        confidence=0.6,
    )
    return Candidate.from_definition(definition, matched_alias="活跃用户", match_kind="name_exact", match_score=0.5)


class SystemPromptTest(unittest.TestCase):
    def test_prompt_requires_json_and_key_fields(self) -> None:
        for token in ("equivalent", "chosen_id", "rationale", "confidence", "requires_human", "conflicts"):
            self.assertIn(token, ARBITRATION_SYSTEM_PROMPT)
        self.assertIn("只输出一个 JSON 对象", ARBITRATION_SYSTEM_PROMPT)

    def test_build_prompt_embeds_candidates_and_precheck(self) -> None:
        prompt = build_arbitration_prompt(
            "上个月活跃用户数",
            [candidate()],
            {"conflict": False, "reasons": []},
            context="口径评审会结论：优先登录口径",
            max_definition_chars=20,
        )
        payload = json.loads(prompt[prompt.index("{") : prompt.rindex("}") + 1])
        self.assertEqual(payload["user_query"], "上个月活跃用户数")
        self.assertEqual(payload["candidates"][0]["id"], "active_user.7d_login")
        self.assertTrue(payload["candidates"][0]["definition_text"].endswith("…"))
        self.assertEqual(payload["precheck_signals"]["conflict"], False)
        self.assertIn("优先登录口径", payload["extra_context"])
        self.assertIn("只输出约定的 JSON 对象", prompt)

    def test_build_prompt_without_precheck(self) -> None:
        prompt = build_arbitration_prompt("q", [candidate()], None)
        self.assertIn('"precheck_signals": {}', prompt)


class ParseArbitrationResponseTest(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "equivalent": False,
            "chosen_id": "active_user.7d_login",
            "rationale": "窗口不同：7 天 vs 30 天。",
            "confidence": 0.72,
            "requires_human": False,
            "conflicts": [
                {"ids": ["a", "b"], "type": "semantic", "detail": "时间窗口不同"}
            ],
        }

    def test_raw_json(self) -> None:
        parsed = parse_arbitration_response(json.dumps(self.valid_payload(), ensure_ascii=False))
        self.assertEqual(parsed["chosen_id"], "active_user.7d_login")
        self.assertEqual(parsed["confidence"], 0.72)
        self.assertFalse(parsed["confidence_missing"])
        self.assertEqual(len(parsed["conflicts"]), 1)

    def test_fenced_json(self) -> None:
        raw = "```json\n" + json.dumps(self.valid_payload(), ensure_ascii=False) + "\n```"
        parsed = parse_arbitration_response(raw)
        self.assertEqual(parsed["chosen_id"], "active_user.7d_login")

    def test_prose_around_json(self) -> None:
        raw = "我的判断如下：\n" + json.dumps(self.valid_payload(), ensure_ascii=False) + "\n如需复核请告知。"
        parsed = parse_arbitration_response(raw)
        self.assertEqual(parsed["confidence"], 0.72)

    def test_braces_inside_strings_do_not_break_extraction(self) -> None:
        payload = self.valid_payload()
        payload["rationale"] = '定义里写了 "WHERE a = {x}"，两边不一致。'
        parsed = parse_arbitration_response("前缀 " + json.dumps(payload, ensure_ascii=False) + " 后缀")
        self.assertIn("{x}", parsed["rationale"])

    def test_missing_rationale_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload.pop("rationale")
        with self.assertRaises(ArbitrationParseError):
            parse_arbitration_response(json.dumps(payload))

    def test_empty_output_is_rejected(self) -> None:
        with self.assertRaises(ArbitrationParseError):
            parse_arbitration_response("   ")
        with self.assertRaises(ArbitrationParseError):
            parse_arbitration_response(None)  # type: ignore[arg-type]

    def test_non_json_output_is_rejected(self) -> None:
        with self.assertRaises(ArbitrationParseError) as ctx:
            parse_arbitration_response("口径 A 更好。")
        self.assertIn("cannot parse", str(ctx.exception))

    def test_confidence_as_percentage_is_normalised(self) -> None:
        payload = self.valid_payload()
        payload["confidence"] = 85
        parsed = parse_arbitration_response(json.dumps(payload))
        self.assertAlmostEqual(parsed["confidence"], 0.85, places=6)

    def test_missing_confidence_flag(self) -> None:
        payload = self.valid_payload()
        payload.pop("confidence")
        parsed = parse_arbitration_response(json.dumps(payload))
        self.assertTrue(parsed["confidence_missing"])
        self.assertEqual(parsed["confidence"], 0.0)

    def test_chinese_boolean_strings(self) -> None:
        payload = self.valid_payload()
        payload["requires_human"] = "是"
        payload["equivalent"] = "否"
        parsed = parse_arbitration_response(json.dumps(payload, ensure_ascii=False))
        self.assertTrue(parsed["requires_human"])
        self.assertFalse(parsed["equivalent"])

    def test_string_null_choice(self) -> None:
        payload = self.valid_payload()
        payload["chosen_id"] = "null"
        parsed = parse_arbitration_response(json.dumps(payload))
        self.assertIsNone(parsed["chosen_id"])

    def test_rationale_is_truncated(self) -> None:
        payload = self.valid_payload()
        payload["rationale"] = "很长的理由" * 200
        parsed = parse_arbitration_response(json.dumps(payload, ensure_ascii=False))
        self.assertLessEqual(len(parsed["rationale"]), MAX_RATIONALE_CHARS)

    def test_validate_rejects_non_object(self) -> None:
        with self.assertRaises(ArbitrationParseError):
            validate_arbitration_payload(["not", "a", "dict"])


if __name__ == "__main__":
    unittest.main()
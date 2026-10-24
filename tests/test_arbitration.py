"""Tests for LLM arbitration (acceptance criterion #1) and its degradation paths."""

from __future__ import annotations

import json
import unittest

from core.errors import LLMError
from core.llm import StaticLLMClient
from core.models import Candidate, MetricDefinition
from core.prompts import ARBITRATION_SYSTEM_PROMPT, PROMPT_VERSION
from tests.support import arbitration_json, make_service


def two_candidates(*, same_sql: bool = False, confidence_first: float = 0.6):
    sql_a = "SELECT COUNT(DISTINCT user_id) FROM t"
    sql_b = sql_a if same_sql else "SELECT COUNT(DISTINCT buyer_id) FROM t"
    first = MetricDefinition.create(
        name="活跃用户",
        definition_text="过去 7 天内登录过的去重用户数",
        metric_id="caliber.a",
        sql_fragment=sql_a,
        confidence=confidence_first,
    )
    second = MetricDefinition.create(
        name="活跃用户",
        definition_text="过去 7 天内登录过的去重用户数",
        metric_id="caliber.b",
        sql_fragment=sql_b,
        confidence=0.5,
    )
    return [
        Candidate.from_definition(first, matched_alias="活跃用户", match_kind="name_exact", match_score=1.0),
        Candidate.from_definition(second, matched_alias="活跃用户", match_kind="name_exact", match_score=1.0),
    ]


class ArbitrateAcceptanceTest(unittest.TestCase):
    def test_acceptance_arbitrate_returns_chosen_and_rationale(self) -> None:
        llm = StaticLLMClient(arbitration_json())
        service, _ = make_service(llm=llm)
        resolved = service.resolve("上个月活跃用户数")

        result = service.arbitrate(resolved.candidates, query="上个月活跃用户数")

        self.assertTrue(result.resolved, msg=f"strategy={result.strategy} error={result.error}")
        self.assertEqual(result.chosen_id, "active_user.30d_order")
        self.assertEqual(result.chosen_name, "活跃用户")
        self.assertTrue(result.rationale)
        self.assertFalse(result.requires_human)
        self.assertEqual(result.strategy, "llm_arbitration")
        self.assertGreaterEqual(result.confidence, 0.6)
        self.assertEqual(result.prompt_version, PROMPT_VERSION)

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["system"], ARBITRATION_SYSTEM_PROMPT)
        self.assertIn("上个月活跃用户数", llm.calls[0]["prompt"])
        self.assertIn("active_user.7d_login", llm.calls[0]["prompt"])
        self.assertIn("active_user.30d_order", llm.calls[0]["prompt"])

        payload = result.to_dict()
        json.dumps(payload)
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["chosen_id"], "active_user.30d_order")

    def test_fenced_json_answer_is_accepted(self) -> None:
        answer = "好的，仲裁结果如下：\n```json\n" + arbitration_json() + "\n```\n希望有帮助。"
        service, _ = make_service(llm=StaticLLMClient(answer))
        candidates = service.resolve("上个月活跃用户数").candidates
        result = service.arbitrate(candidates, query="上个月活跃用户数")
        self.assertTrue(result.resolved, msg=result.error)
        self.assertEqual(result.strategy, "llm_arbitration")


class ArbirationDegradationTest(unittest.TestCase):
    def _service_with(self, response):
        llm = StaticLLMClient(response)
        service, _store = make_service(llm=llm)
        return service, llm

    def _candidates(self, same_sql: bool = False):
        return two_candidates(same_sql=same_sql)

    def answer(self, **overrides):
        """A model answer that refers to the synthetic caliber ids used here."""
        payload = {"chosen_id": "caliber.a"}
        payload.update(overrides)
        return arbitration_json(**payload)

    def test_low_model_confidence_requires_human(self) -> None:
        service, _ = self._service_with(self.answer(confidence=0.4))
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertFalse(result.resolved)
        self.assertTrue(result.requires_human)
        self.assertEqual(result.chosen_id, "caliber.a")
        self.assertEqual(result.signals["human_threshold"], 0.6)

    def test_model_request_human_is_honoured(self) -> None:
        service, _ = self._service_with(self.answer(requires_human=True, confidence=0.9))
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertFalse(result.resolved)
        self.assertTrue(result.requires_human)

    def test_missing_confidence_forces_human(self) -> None:
        service, _ = self._service_with(self.answer(confidence=None))
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertTrue(result.requires_human)
        self.assertFalse(result.resolved)
        self.assertTrue(result.signals["confidence_missing"])

    def test_no_choice_with_conflict_requires_human(self) -> None:
        service, _ = self._service_with(
            self.answer(chosen_id=None, equivalent=False, requires_human=True)
        )
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertEqual(result.strategy, "llm_no_choice")
        self.assertTrue(result.requires_human)
        self.assertIsNone(result.chosen_id)

    def test_equivalent_without_choice_picks_highest_confidence(self) -> None:
        service, _ = self._service_with(
            self.answer(chosen_id=None, equivalent=True, requires_human=False, confidence=0.9)
        )
        candidates = self._candidates(same_sql=True)
        result = service.arbitrate(candidates, query="过去 7 天活跃用户数")
        self.assertEqual(result.strategy, "llm_equivalent_auto")
        self.assertEqual(result.chosen_id, "caliber.a")
        self.assertTrue(result.resolved)

    def test_invalid_choice_degrades(self) -> None:
        service, _ = self._service_with(arbitration_json(chosen_id="ghost.metric"))
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertEqual(result.strategy, "llm_invalid_choice")
        self.assertTrue(result.requires_human)
        self.assertIsNone(result.chosen_id)
        self.assertIn("ghost.metric", result.error)

    def test_unparsable_answer_degrades(self) -> None:
        service, _ = self._service_with("我觉得口径 A 更好，但我不打算输出 JSON。")
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertEqual(result.strategy, "llm_unparsable")
        self.assertTrue(result.requires_human)
        self.assertFalse(result.resolved)

    def test_model_error_degrades(self) -> None:
        service, _ = self._service_with(LLMError("upstream 502"))
        result = service.arbitrate(self._candidates(), query="上个月活跃用户数")
        self.assertEqual(result.strategy, "llm_error")
        self.assertIn("upstream 502", result.error)
        self.assertEqual(result.signals["degraded"], True)

    def test_no_model_degrades_with_ranked_fallback(self) -> None:
        service, _ = make_service(llm=None)
        candidates = service.resolve("上个月活跃用户数").candidates
        result = service.arbitrate(candidates, query="上个月活跃用户数")
        self.assertEqual(result.strategy, "llm_unavailable")
        self.assertTrue(result.requires_human)
        self.assertEqual(result.fallback_order[0], "active_user.7d_login")
        self.assertIn("未配置可用的模型网关", result.rationale)

    def test_no_candidates(self) -> None:
        service, _ = make_service(llm=StaticLLMClient(arbitration_json()))
        result = service.arbitrate([], query="上个月活跃用户数")
        self.assertEqual(result.strategy, "no_candidates")
        self.assertTrue(result.requires_human)
        self.assertFalse(result.conflict)


if __name__ == "__main__":
    unittest.main()
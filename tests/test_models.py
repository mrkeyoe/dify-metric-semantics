"""Tests for dataclasses: validation, id generation, serialisation."""

from __future__ import annotations

import unittest

from core.errors import ValidationError
from core.models import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    Candidate,
    ConflictReport,
    FeedbackResult,
    MetricDefinition,
    clamp_confidence,
    make_metric_id,
)


class MakeMetricIdTest(unittest.TestCase):
    def test_stable_for_same_input(self) -> None:
        self.assertEqual(
            make_metric_id("活跃用户", "过去 7 天内登录过的用户数"),
            make_metric_id("活跃用户", "过去 7 天内登录过的用户数"),
        )

    def test_distinct_for_different_definitions(self) -> None:
        first = make_metric_id("活跃用户", "过去 7 天内登录过的用户数")
        second = make_metric_id("活跃用户", "过去 30 天内下单的用户数")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("活跃用户-"), first)
        self.assertEqual(len(first.rsplit("-", 1)[1]), 8)

    def test_hint_changes_slug(self) -> None:
        self.assertTrue(make_metric_id("活跃用户", "x", hint="active_user").startswith("active-user-"))


class ClampConfidenceTest(unittest.TestCase):
    def test_clamps_into_range(self) -> None:
        self.assertEqual(clamp_confidence(1.5), CONFIDENCE_MAX)
        self.assertEqual(clamp_confidence(-3), CONFIDENCE_MIN)
        self.assertEqual(clamp_confidence("0.42"), 0.42)

    def test_rejects_non_numeric(self) -> None:
        with self.assertRaises(ValidationError):
            clamp_confidence("high")

    def test_rejects_nan(self) -> None:
        with self.assertRaises(ValidationError):
            clamp_confidence(float("nan"))


class MetricDefinitionTest(unittest.TestCase):
    def test_create_fills_defaults(self) -> None:
        definition = MetricDefinition.create(
            name="活跃用户",
            definition_text="过去 7 天内登录过的去重用户数",
            aliases=["活跃用户", "DAU"],
            source_tables=["dwd.user_login_log"],
        )
        self.assertTrue(definition.id.startswith("活跃用户-"))
        self.assertEqual(definition.aliases, ["活跃用户", "DAU"])
        self.assertEqual(definition.confidence, 0.5)
        self.assertTrue(definition.created_at)
        self.assertEqual(definition.stats["successes"], 0)
        self.assertEqual(definition.stats["failures"], 0)

    def test_requires_name_and_definition(self) -> None:
        with self.assertRaises(ValidationError):
            MetricDefinition.create(name="", definition_text="x")
        with self.assertRaises(ValidationError):
            MetricDefinition.create(name="x", definition_text="  ")

    def test_empty_sql_fragment_becomes_none(self) -> None:
        definition = MetricDefinition.create(name="x", definition_text="y", sql_fragment="   ")
        self.assertIsNone(definition.sql_fragment)

    def test_roundtrip(self) -> None:
        original = MetricDefinition.create(
            name="成交金额",
            definition_text="已支付订单的实付金额之和",
            aliases=["GMV"],
            source_tables=["dwd.order_detail"],
            sql_fragment="SELECT SUM(pay_amount) FROM dwd.order_detail",
            owner="交易组",
            confidence=0.7,
            metadata={"source": "test"},
        )
        restored = MetricDefinition.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(restored.owner, "交易组")

    def test_from_dict_ignores_unknown_keys(self) -> None:
        payload = MetricDefinition.create(name="x", definition_text="y").to_dict()
        payload["something_else"] = 1
        MetricDefinition.from_dict(payload)

    def test_from_dict_rejects_non_object(self) -> None:
        with self.assertRaises(ValidationError):
            MetricDefinition.from_dict([])  # type: ignore[arg-type]


class CandidateTest(unittest.TestCase):
    def _definition(self) -> MetricDefinition:
        return MetricDefinition.create(
            name="活跃用户",
            definition_text="过去 7 天内登录过的去重用户数",
            aliases=["日活"],
            sql_fragment="SELECT COUNT(DISTINCT user_id) FROM t",
            confidence=0.6,
        )

    def test_from_definition_keeps_provenance(self) -> None:
        candidate = Candidate.from_definition(
            self._definition(), matched_alias="日活", match_kind="alias_exact", match_score=1.0
        )
        self.assertEqual(candidate.matched_alias, "日活")
        self.assertEqual(candidate.match_kind, "alias_exact")
        self.assertEqual(candidate.confidence, 0.6)

    def test_roundtrip(self) -> None:
        candidate = Candidate.from_definition(
            self._definition(), matched_alias="日活", match_kind="alias_substring", match_score=0.25
        )
        restored = Candidate.from_dict(candidate.to_dict())
        self.assertEqual(restored.to_dict(), candidate.to_dict())

    def test_from_dict_requires_id_and_definition(self) -> None:
        with self.assertRaises(ValidationError):
            Candidate.from_dict({"definition_text": "x"})
        with self.assertRaises(ValidationError):
            Candidate.from_dict({"id": "a"})


class ResultDataclassTest(unittest.TestCase):
    def test_resolve_result_to_dict_shape(self) -> None:
        from core.models import ResolveResult

        report = ConflictReport(conflict=True, reasons=["r"], threshold=0.35)
        result = ResolveResult(
            query="q",
            matched=True,
            conflict=True,
            candidates=[],
            conflict_report=report,
            resolution_hint="arbitrate",
        )
        payload = result.to_dict()
        self.assertEqual(payload["candidate_count"], 0)
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["threshold"], 0.35)
        self.assertEqual(payload["conflicts"]["reasons"], ["r"])

    def test_feedback_result_delta(self) -> None:
        payload = FeedbackResult(
            metric_id="a",
            old_confidence=0.6,
            new_confidence=0.48,
            signal=0.0,
            alpha=0.2,
            successes=0,
            failures=1,
        ).to_dict()
        self.assertAlmostEqual(payload["delta"], -0.12, places=6)


if __name__ == "__main__":
    unittest.main()
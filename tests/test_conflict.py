"""Tests for deterministic conflict detection between candidate calibers."""

from __future__ import annotations

import unittest

from core.conflict import conflicting_ids, describe_for_prompt, detect_conflicts, summarize_report
from core.models import Candidate, MetricDefinition
from tests.support import make_service


def candidate(
    metric_id: str,
    definition_text: str,
    *,
    sql_fragment: str | None = None,
    source_tables: list[str] | None = None,
    confidence: float = 0.5,
) -> Candidate:
    definition = MetricDefinition.create(
        name="活跃用户",
        definition_text=definition_text,
        metric_id=metric_id,
        sql_fragment=sql_fragment,
        source_tables=source_tables or [],
        confidence=confidence,
    )
    return Candidate.from_definition(definition, matched_alias="活跃用户", match_kind="name_exact", match_score=1.0)


class DetectConflictsTest(unittest.TestCase):
    def test_acceptance_two_active_user_calibers_conflict(self) -> None:
        service, _ = make_service()
        candidates = service.resolve("上个月活跃用户数").candidates
        self.assertEqual(len(candidates), 2)

        report = detect_conflicts(candidates, threshold=0.35)
        self.assertTrue(report.conflict)
        reasons = " ".join(report.reasons)
        self.assertIn("sql_fragment_mismatch", reasons)
        self.assertIn("numeric_scope_mismatch", reasons)
        self.assertIn("definition_distance", reasons)
        self.assertEqual(set(conflicting_ids(report)), {"active_user.7d_login", "active_user.30d_order"})
        self.assertIn("conflict detected", summarize_report(report))

    def test_identical_definitions_and_sql_do_not_conflict(self) -> None:
        sql = "SELECT COUNT(DISTINCT user_id) FROM dwd.user_login_log"
        left = candidate("a", "过去 7 天内登录过的去重用户数", sql_fragment=sql)
        right = candidate("b", "过去 7 天内登录过的去重用户数", sql_fragment=sql)
        report = detect_conflicts([left, right], threshold=0.35)
        self.assertFalse(report.conflict)
        self.assertEqual(report.reasons, [])
        self.assertEqual(report.max_semantic_distance, 0.0)
        self.assertIn("look equivalent", summarize_report(report))

    def test_different_sql_alone_is_a_conflict(self) -> None:
        left = candidate("a", "过去 7 天内登录过的去重用户数", sql_fragment="SELECT COUNT(DISTINCT user_id) FROM t")
        right = candidate("b", "过去 7 天内登录过的去重用户数", sql_fragment="SELECT COUNT(DISTINCT buyer_id) FROM t")
        report = detect_conflicts([left, right], threshold=0.35)
        self.assertTrue(report.conflict)
        self.assertEqual(report.reasons, ["sql_fragment_mismatch:a~b"])
        self.assertEqual(report.max_semantic_distance, 0.0)

    def test_missing_sql_fragment_is_only_a_note(self) -> None:
        left = candidate("a", "过去 7 天内登录过的去重用户数")
        right = candidate("b", "过去 7 天内登录过的去重用户数")
        report = detect_conflicts([left, right], threshold=0.35)
        self.assertFalse(report.conflict)
        self.assertIn("sql_fragment_missing:a", report.notes)
        self.assertIn("sql_fragment_missing:b", report.notes)

    def test_disjoint_source_tables_is_only_a_note(self) -> None:
        left = candidate("a", "过去 7 天内登录过的去重用户数", source_tables=["dwd.a"])
        right = candidate("b", "过去 7 天内登录过的去重用户数", source_tables=["dwd.b"])
        report = detect_conflicts([left, right], threshold=0.35)
        self.assertFalse(report.conflict)
        self.assertIn("source_tables_disjoint:a~b", report.notes)

    def test_high_threshold_suppresses_distance_conflict_but_not_sql(self) -> None:
        left = candidate("a", "过去 7 天内登录过的去重用户数", sql_fragment="SELECT 1")
        right = candidate("b", "完全不同的定义文本", sql_fragment="SELECT 2")
        lenient = detect_conflicts([left, right], threshold=1.0)
        self.assertTrue(lenient.conflict)
        self.assertIn("sql_fragment_mismatch:a~b", lenient.reasons)
        self.assertFalse(any(reason.startswith("definition_distance") for reason in lenient.reasons))

    def test_single_candidate_has_no_pairs(self) -> None:
        report = detect_conflicts([candidate("a", "x")], threshold=0.35)
        self.assertFalse(report.conflict)
        self.assertEqual(report.pairs, [])
        self.assertIn("only one candidate", summarize_report(report))

    def test_describe_for_prompt_is_json_ready(self) -> None:
        import json

        left = candidate("a", "过去 7 天内登录过的去重用户数", sql_fragment="SELECT 1")
        right = candidate("b", "过去 30 天内下单的用户数", sql_fragment="SELECT 2")
        report = detect_conflicts([left, right], threshold=0.35)
        payload = describe_for_prompt(report, [left, right])
        json.dumps(payload)
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["candidate_ids"], ["a", "b"])
        self.assertEqual(payload["pairs"][0]["ids"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
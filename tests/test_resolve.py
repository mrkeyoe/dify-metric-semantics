"""Tests for matching and the resolve pipeline (acceptance criterion #1)."""

from __future__ import annotations

import unittest

from core.errors import DuplicateMetricError, ValidationError
from tests.support import make_service


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.store = make_service()

    def test_acceptance_query_returns_conflict_signal(self) -> None:
        result = self.service.resolve("上个月活跃用户数")

        self.assertTrue(result.matched)
        self.assertTrue(result.conflict)
        self.assertEqual(result.resolution_hint, "arbitrate")
        self.assertEqual(
            {candidate.id for candidate in result.candidates},
            {"active_user.7d_login", "active_user.30d_order"},
        )
        self.assertEqual(len(result.conflict_report.pairs), 1)
        self.assertGreater(result.conflict_report.max_semantic_distance, 0.35)

        payload = result.to_dict()
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["candidate_count"], 2)
        self.assertTrue(payload["conflicts"]["conflict"])
        self.assertIn("sql_fragment_mismatch", " ".join(payload["conflicts"]["reasons"]))

    def test_alias_only_query_matches_one_caliber(self) -> None:
        result = self.service.resolve("日活是多少")
        self.assertTrue(result.matched)
        self.assertEqual([candidate.id for candidate in result.candidates], ["active_user.7d_login"])
        self.assertFalse(result.conflict)
        self.assertEqual(result.resolution_hint, "use_candidate")

    def test_latin_alias_is_matched_case_insensitively(self) -> None:
        result = self.service.resolve("本月 GMV 多少")
        self.assertEqual([candidate.id for candidate in result.candidates], ["gmv.paid_order_amount"])

    def test_metric_hint_filters_candidates(self) -> None:
        result = self.service.resolve("上个月活跃用户数", metric_name="日活")
        self.assertEqual([candidate.id for candidate in result.candidates], ["active_user.7d_login"])
        self.assertTrue(any(note.startswith("metric_hint_filter:") for note in result.notes))
        self.assertFalse(result.conflict)

    def test_metric_hint_without_match_keeps_candidates(self) -> None:
        result = self.service.resolve("上个月活跃用户数", metric_name="退款率")
        self.assertEqual(len(result.candidates), 2)
        self.assertIn("metric_hint_no_match:退款率", result.notes)

    def test_top_k_truncates_candidates(self) -> None:
        result = self.service.resolve("上个月活跃用户数", top_k=1)
        self.assertEqual(len(result.candidates), 1)
        self.assertTrue(any(note.startswith("candidates_truncated:") for note in result.notes))

    def test_unmatched_query_asks_for_registration(self) -> None:
        result = self.service.resolve("退款率是多少")
        self.assertFalse(result.matched)
        self.assertFalse(result.conflict)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.resolution_hint, "register_metric")
        self.assertIsInstance(result.suggestions, list)

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.resolve("   ")

    def test_resolve_to_dict_exposes_summary_and_llm_flag(self) -> None:
        payload = self.service.resolve_to_dict("上个月活跃用户数")
        self.assertIn("summary", payload)
        self.assertIn("conflict detected", payload["summary"])
        self.assertFalse(payload["llm_available"])


class RegistrationTest(unittest.TestCase):
    def test_duplicate_id_requires_replace(self) -> None:
        service, _ = make_service(seed=False)
        service.register_metric(name="活跃用户", definition_text="过去 7 天内登录过的去重用户数")
        with self.assertRaises(DuplicateMetricError):
            service.register_metric(name="活跃用户", definition_text="过去 7 天内登录过的去重用户数")
        replaced = service.register_metric(
            name="活跃用户", definition_text="过去 7 天内登录过的去重用户数", confidence=0.9, replace=True
        )
        self.assertEqual(replaced.confidence, 0.9)

    def test_register_and_get_metric(self) -> None:
        service, _ = make_service(seed=False)
        stored = service.register_metric(
            name="活跃用户",
            definition_text="过去 30 天内下单的用户数",
            aliases=["活跃买家"],
            source_tables=["dwd.order_detail"],
            owner="交易组",
            confidence=0.4,
            metric_id="active_user.30d_order",
        )
        fetched = service.get_metric(stored.id)
        self.assertEqual(fetched.aliases, ["活跃买家"])
        self.assertEqual(len(service.list_metrics()), 1)


if __name__ == "__main__":
    unittest.main()
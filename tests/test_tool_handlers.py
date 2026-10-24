"""Tests for the Dify tool handlers (pure dict-in / dict-out layer)."""

from __future__ import annotations

import json
import unittest

from core.llm import StaticLLMClient
from core.tool_handlers import TOOL_HANDLERS, ToolInputError, dispatch
from tests.support import arbitration_json, make_service


class ResolveHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = make_service()

    def test_conflict_is_reported(self) -> None:
        payload = dispatch("resolve_metric", self.service, {"query": "上个月活跃用户数"})
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["next_action"], "arbitrate")
        self.assertIn("guidance", payload)
        json.dumps(payload)  # must stay JSON-serialisable for Dify

    def test_missing_query_is_a_tool_input_error(self) -> None:
        payload = dispatch("resolve_metric", self.service, {})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "ToolInputError")
        self.assertTrue(payload["retryable"])

    def test_out_of_range_top_k_is_rejected(self) -> None:
        payload = dispatch("resolve_metric", self.service, {"query": "GMV", "top_k": 0})
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["retryable"])

    def test_metric_hint_is_forwarded(self) -> None:
        payload = dispatch("resolve_metric", self.service, {"query": "活跃用户", "metric_hint": "活跃买家"})
        self.assertTrue(payload["ok"])
        self.assertEqual([c["id"] for c in payload["candidates"]], ["active_user.30d_order"])


class ArbitrateHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = StaticLLMClient(arbitration_json(rationale="窗口不同，按口径B更贴近“上个月”。"))
        self.service, _ = make_service(llm=self.llm)

    def test_resolves_from_query(self) -> None:
        payload = dispatch("arbitrate", self.service, {"query": "上个月活跃用户数"})
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["conflict"])
        self.assertEqual(payload["candidate_source"], "query_resolve")
        self.assertEqual(payload["chosen_id"], "active_user.30d_order")
        self.assertTrue(payload["rationale"])
        self.assertEqual(payload["next_action"], "record_outcome")

    def test_resolves_from_candidate_ids(self) -> None:
        payload = dispatch(
            "arbitrate",
            self.service,
            {"candidate_ids": "active_user.7d_login,active_user.30d_order"},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["candidate_source"], "candidate_ids")
        self.assertEqual(len(self.llm.calls), 1)

    def test_resolves_from_candidates_json(self) -> None:
        resolve_payload = dispatch("resolve_metric", self.service, {"query": "上个月活跃用户数"})
        only_one = dispatch(
            "arbitrate",
            self.service,
            {"candidates_json": json.dumps([resolve_payload["candidates"][1]])},
        )
        self.assertTrue(only_one["ok"])
        self.assertEqual(only_one["candidate_source"], "candidates_json")
        self.assertEqual(only_one["candidate_count"], 1)

    def test_unknown_candidate_id_is_reported(self) -> None:
        payload = dispatch("arbitrate", self.service, {"candidate_ids": "ghost.metric"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "MetricNotFoundError")
        self.assertFalse(payload["retryable"])

    def test_missing_source_is_reported(self) -> None:
        payload = dispatch("arbitrate", self.service, {"context": "只有上下文"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "ToolInputError")

    def test_requires_human_points_to_ask_human(self) -> None:
        service, _ = make_service(llm=StaticLLMClient(arbitration_json(confidence=0.3)))
        payload = dispatch("arbitrate", service, {"query": "上个月活跃用户数"})
        self.assertTrue(payload["requires_human"])
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["next_action"], "ask_human")


class RecordOutcomeHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = make_service()

    def test_failure_returns_new_confidence(self) -> None:
        payload = dispatch(
            "record_outcome",
            self.service,
            {"metric_id": "active_user.7d_login", "outcome": "failure", "note": "SQL 报错"},
        )
        self.assertTrue(payload["ok"])
        self.assertAlmostEqual(payload["new_confidence"], 0.48, places=6)
        self.assertEqual(payload["next_action"], "resolve_metric")
        self.assertLess(payload["delta"], 0)

    def test_unknown_metric_is_not_retryable(self) -> None:
        payload = dispatch("record_outcome", self.service, {"metric_id": "nope", "outcome": "success"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "MetricNotFoundError")
        self.assertFalse(payload["retryable"])

    def test_missing_signal_is_rejected(self) -> None:
        payload = dispatch("record_outcome", self.service, {"metric_id": "active_user.7d_login"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "ToolInputError")


class ListMetricsHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service, _ = make_service()

    def test_lists_all_seeded_metrics(self) -> None:
        payload = dispatch("list_metrics", self.service, {})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["health"]["metric_count"], 3)

    def test_query_filter(self) -> None:
        payload = dispatch("list_metrics", self.service, {"query": "GMV"})
        self.assertEqual([m["id"] for m in payload["metrics"]], ["gmv.paid_order_amount"])


class DispatchTest(unittest.TestCase):
    def test_handlers_are_registered(self) -> None:
        self.assertEqual(
            set(TOOL_HANDLERS),
            {"resolve_metric", "arbitrate", "record_outcome", "list_metrics"},
        )

    def test_unknown_tool_is_reported(self) -> None:
        service, _ = make_service()
        payload = dispatch("drop_tables", service, {})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "ToolInputError")
        self.assertIn("resolve_metric", payload["message"])

    def test_tool_input_error_subclasses_value_error(self) -> None:
        self.assertTrue(issubclass(ToolInputError, ValueError))


if __name__ == "__main__":
    unittest.main()
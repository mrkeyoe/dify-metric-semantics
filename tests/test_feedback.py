"""Tests for the EMA learning loop (acceptance criterion #2)."""

from __future__ import annotations

import unittest

from core.errors import MetricNotFoundError, ValidationError
from core.feedback import SIGNAL_FAILURE, SIGNAL_SUCCESS, ema_update, signal_from_outcome
from tests.support import make_service


class EmaMathTest(unittest.TestCase):
    def test_failure_moves_down(self) -> None:
        self.assertAlmostEqual(ema_update(0.6, SIGNAL_FAILURE, 0.2), 0.48, places=6)

    def test_success_moves_up(self) -> None:
        self.assertAlmostEqual(ema_update(0.6, SIGNAL_SUCCESS, 0.2), 0.68, places=6)

    def test_alpha_one_takes_signal(self) -> None:
        self.assertAlmostEqual(ema_update(0.2, SIGNAL_SUCCESS, 1.0), 0.99, places=6)  # ceiling clamp
        self.assertAlmostEqual(ema_update(0.2, SIGNAL_FAILURE, 1.0), 0.01, places=6)  # floor clamp

    def test_signal_from_outcome(self) -> None:
        self.assertEqual(signal_from_outcome(True), 1.0)
        self.assertEqual(signal_from_outcome(False), 0.0)

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            ema_update(0.6, 0.0, 0.0)  # alpha must be > 0
        with self.assertRaises(ValidationError):
            ema_update(0.6, 0.0, 1.5)  # alpha <= 1
        with self.assertRaises(ValidationError):
            ema_update(0.6, 1.2, 0.2)  # signal in [0, 1]
        with self.assertRaises(ValidationError):
            ema_update(1.5, 0.0, 0.2)  # current in [0, 1]


class FeedbackLoopAcceptanceTest(unittest.TestCase):
    def test_acceptance_failed_execution_lowers_confidence_in_next_resolve(self) -> None:
        service, _ = make_service()

        before = service.resolve("上个月活跃用户数")
        before_confidence = {candidate.id: candidate.confidence for candidate in before.candidates}
        self.assertEqual(before_confidence["active_user.7d_login"], 0.6)

        feedback = service.record_outcome(
            "active_user.7d_login",
            success=False,
            note="SQL 执行失败：dwd.user_login_log 无 login_time 字段",
        )
        self.assertAlmostEqual(feedback.old_confidence, 0.6, places=6)
        self.assertAlmostEqual(feedback.new_confidence, 0.48, places=6)
        self.assertEqual(feedback.failures, 1)
        self.assertLess(feedback.new_confidence, feedback.old_confidence)

        after = service.resolve("上个月活跃用户数")
        after_confidence = {candidate.id: candidate.confidence for candidate in after.candidates}
        self.assertAlmostEqual(after_confidence["active_user.7d_login"], 0.48, places=6)
        self.assertLess(after_confidence["active_user.7d_login"], before_confidence["active_user.7d_login"])
        self.assertLess(
            after_confidence["active_user.7d_login"],
            after_confidence["active_user.30d_order"],
        )
        # The conflict signal itself is structural and must not disappear.
        self.assertTrue(after.conflict)

    def test_repeated_failures_keep_decaying(self) -> None:
        service, _ = make_service()
        first = service.record_outcome("active_user.7d_login", success=False)
        second = service.record_outcome("active_user.7d_login", success=False)
        second_again = service.record_outcome("active_user.7d_login", success=False)
        self.assertAlmostEqual(first.new_confidence, 0.48, places=6)
        self.assertAlmostEqual(second.new_confidence, 0.384, places=6)
        self.assertAlmostEqual(second_again.new_confidence, 0.3072, places=6)

    def test_success_raises_confidence(self) -> None:
        service, _ = make_service()
        feedback = service.record_outcome("active_user.7d_login", success=True)
        self.assertAlmostEqual(feedback.new_confidence, 0.68, places=6)
        self.assertEqual(feedback.successes, 1)

    def test_custom_alpha_is_applied(self) -> None:
        service, _ = make_service()
        feedback = service.record_outcome("active_user.7d_login", success=False, alpha=0.5)
        self.assertAlmostEqual(feedback.new_confidence, 0.3, places=6)
        self.assertEqual(feedback.alpha, 0.5)

    def test_explicit_signal_is_supported(self) -> None:
        service, _ = make_service()
        feedback = service.record_outcome("active_user.7d_login", signal=0.5)
        self.assertAlmostEqual(feedback.new_confidence, 0.58, places=6)

    def test_history_and_stats_are_recorded(self) -> None:
        service, store = make_service()
        service.record_outcome("active_user.7d_login", success=False, note="bad column")
        history = store.history("active_user.7d_login")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["signal"], 0.0)
        self.assertEqual(history[0]["note"], "bad column")
        stored = store.get("active_user.7d_login")
        self.assertEqual(stored.stats["failures"], 1)
        self.assertIn("last_feedback_at", stored.stats)

    def test_unknown_metric_is_rejected(self) -> None:
        service, _ = make_service()
        with self.assertRaises(MetricNotFoundError):
            service.record_outcome("does.not.exist", success=True)

    def test_missing_signal_is_rejected(self) -> None:
        service, _ = make_service()
        with self.assertRaises(ValidationError):
            service.record_outcome("active_user.7d_login")

    def test_contradictory_success_and_signal_is_rejected(self) -> None:
        service, _ = make_service()
        with self.assertRaises(ValidationError):
            service.record_outcome("active_user.7d_login", success=False, signal=0.9)


if __name__ == "__main__":
    unittest.main()
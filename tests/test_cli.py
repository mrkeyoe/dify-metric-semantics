"""Tests for the offline CLI (`python -m core ...`), used as a manual smoke test."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from core.__main__ import main


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = os.path.join(self._tmp.name, "metrics.json")

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--store", self.store, *argv])
        return code, json.loads(buffer.getvalue())

    def test_seed_resolve_arbitrate_record_flow(self) -> None:
        code, seeded = self.run_cli("seed")
        self.assertEqual(code, 0)
        self.assertEqual(len(seeded["registered"]), 3)

        code, resolved = self.run_cli("resolve", "上个月活跃用户数")
        self.assertEqual(code, 0)
        self.assertTrue(resolved["conflict"])
        self.assertEqual(resolved["candidate_count"], 2)

        code, arbitrated = self.run_cli("arbitrate", "上个月活跃用户数", "--stub")
        self.assertEqual(code, 0)
        self.assertEqual(arbitrated["chosen_id"], "active_user.7d_login")
        self.assertTrue(arbitrated["rationale"])

        code, recorded = self.run_cli(
            "record", "active_user.7d_login", "--outcome", "failure", "--note", "字段缺失"
        )
        self.assertEqual(code, 0)
        self.assertAlmostEqual(recorded["feedback"]["new_confidence"], 0.48, places=6)

        code, listed = self.run_cli("list")
        self.assertEqual(code, 0)
        confidence = {m["id"]: m["confidence"] for m in listed["metrics"]}
        self.assertAlmostEqual(confidence["active_user.7d_login"], 0.48, places=6)

    def test_state_is_persisted_between_invocations(self) -> None:
        self.run_cli("seed")
        self.run_cli("record", "active_user.7d_login", "--outcome", "failure")
        with open(self.store, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["schema_version"], 1)
        self.assertAlmostEqual(document["metrics"]["active_user.7d_login"]["confidence"], 0.48, places=6)

    def test_health(self) -> None:
        self.run_cli("seed")
        code, health = self.run_cli("health")
        self.assertEqual(code, 0)
        self.assertEqual(health["health"]["metric_count"], 3)
        self.assertEqual(health["health"]["llm"], "none")

    def test_unknown_subcommand_exits(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["nope"])

    def test_arbitrate_without_candidates_reports_an_error(self) -> None:
        self.run_cli("seed")
        code, payload = self.run_cli("arbitrate", "退款率是多少", "--stub")
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
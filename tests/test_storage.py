"""Tests for the JSON store: persistence, locking, corruption handling."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from core.errors import DuplicateMetricError, MetricNotFoundError, StorageError
from core.storage import InMemoryMetricStore, JsonMetricStore, open_store
from tests.support import make_config


def definition(metric_id: str = "active_user.7d_login", confidence: float = 0.6):
    from core.models import MetricDefinition

    return MetricDefinition.create(
        name="活跃用户",
        definition_text="过去 7 天内登录过的去重用户数",
        metric_id=metric_id,
        aliases=["日活"],
        sql_fragment="SELECT COUNT(DISTINCT user_id) FROM dwd.user_login_log",
        confidence=confidence,
    )


class InMemoryStoreTest(unittest.TestCase):
    def test_crud_and_volatile_flag(self) -> None:
        store = InMemoryMetricStore(config=make_config())
        self.assertTrue(store.volatile)
        item, created = store.upsert(definition())
        self.assertTrue(created)
        self.assertTrue(store.has(item.id))
        self.assertEqual(store.count(), 1)
        self.assertEqual(store.list_metrics()[0].id, item.id)

        again, created_again = store.upsert(definition())
        self.assertFalse(created_again)
        self.assertEqual(store.count(), 1)

    def test_get_missing_raises(self) -> None:
        store = InMemoryMetricStore(config=make_config())
        with self.assertRaises(MetricNotFoundError) as ctx:
            store.get("nope")
        self.assertIn("not registered", str(ctx.exception))

    def test_find_by_name(self) -> None:
        store = InMemoryMetricStore([definition()], config=make_config())
        self.assertEqual(len(store.find_by_name("活跃用户")), 1)


class JsonStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data" / "metrics.json"
        self.config = make_config(store_path=str(self.path))

    def new_store(self, **kwargs) -> JsonMetricStore:
        return JsonMetricStore(self.path, config=self.config, **kwargs)

    def test_persists_across_instances(self) -> None:
        store = self.new_store()
        item, _ = store.upsert(definition())
        self.assertTrue(self.path.exists())

        reopened = self.new_store()
        loaded = reopened.get(item.id)
        self.assertEqual(loaded.name, "活跃用户")
        self.assertEqual(loaded.confidence, 0.6)
        self.assertEqual(loaded.aliases, ["日活"])

    def test_document_shape(self) -> None:
        store = self.new_store()
        store.upsert(definition())
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("active_user.7d_login", document["metrics"])
        self.assertIsInstance(document["history"], list)

    def test_duplicate_protection(self) -> None:
        store = self.new_store()
        store.upsert(definition())
        with self.assertRaises(DuplicateMetricError):
            store.upsert(definition(confidence=0.9), replace=False)

    def test_reload_after_external_file_change(self) -> None:
        store = self.new_store()
        store.upsert(definition())
        self.assertEqual(store.count(), 1)

        other = self.new_store()
        other.upsert(definition("active_user.30d_order"))
        self.assertEqual(store.count(), 2)  # mtime/size signature triggers a reload

    def test_external_delete_clears_cache(self) -> None:
        store = self.new_store()
        store.upsert(definition())
        os.unlink(self.path)
        self.assertEqual(store.count(), 0)

    def test_feedback_persists_and_audits(self) -> None:
        store = self.new_store()
        store.upsert(definition())
        store.apply_feedback(
            "active_user.7d_login",
            old_confidence=0.6,
            new_confidence=0.48,
            signal=0.0,
            alpha=0.2,
            success=False,
            note="SQL 执行失败",
        )
        reopened = self.new_store()
        loaded = reopened.get("active_user.7d_login")
        self.assertEqual(loaded.confidence, 0.48)
        self.assertEqual(loaded.stats["failures"], 1)
        history = reopened.history("active_user.7d_login")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["note"], "SQL 执行失败")

    def test_history_is_trimmed(self) -> None:
        config = make_config(store_path=str(self.path), max_history=2)
        store = JsonMetricStore(self.path, config=config)
        store.upsert(definition())
        for index in range(4):
            store.apply_feedback(
                "active_user.7d_login",
                old_confidence=0.6,
                new_confidence=0.5 + index * 0.01,
                signal=0.5,
                alpha=0.2,
                success=True,
            )
        self.assertEqual(len(store.history()), 2)

    def test_corrupt_file_raises(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StorageError):
            self.new_store().list_metrics()

    def test_invalid_metric_payload_raises(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"metrics": {"a": {"name": ""}}}), encoding="utf-8")
        with self.assertRaises(StorageError):
            self.new_store().list_metrics()

    def test_stale_lock_is_recovered(self) -> None:
        store = self.new_store(stale_lock_seconds=30)
        store.upsert(definition())  # create the file first
        lock_path = store.lock_path
        lock_path.write_text("99999", encoding="utf-8")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))

        store.upsert(definition("active_user.30d_order"))
        self.assertFalse(lock_path.exists())
        self.assertEqual(store.count(), 2)

    def test_live_lock_times_out(self) -> None:
        store = self.new_store(lock_timeout=0.05, stale_lock_seconds=30)
        store.upsert(definition())
        store.lock_path.write_text("1", encoding="utf-8")
        try:
            with self.assertRaises(StorageError):
                store.upsert(definition("active_user.30d_order"))
        finally:
            store.lock_path.unlink(missing_ok=True)

    def test_open_store_falls_back_to_memory_on_corruption(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("broken", encoding="utf-8")
        fallback = open_store(self.config, allow_volatile=True)
        self.assertIsInstance(fallback, InMemoryMetricStore)
        self.assertIn("not valid JSON", fallback.load_error)

    def test_open_store_raises_when_volatile_disallowed(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(StorageError):
            open_store(self.config, allow_volatile=False)

    def test_open_store_creates_file_store(self) -> None:
        store = open_store(self.config)
        self.assertIsInstance(store, JsonMetricStore)
        self.assertEqual(getattr(store, "load_error", ""), "")


if __name__ == "__main__":
    unittest.main()
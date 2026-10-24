"""Persistence for metric definitions.

Two backends share one interface:

* ``InMemoryMetricStore`` — used by tests, demos and as a volatile fallback when
  the Dify runtime has no writable volume.
* ``JsonMetricStore`` — single JSON document, written atomically
  (``tmp`` + ``os.replace``) under an advisory lock file, so several agent
  processes can share one workspace without corrupting the file.

**Why JSON and not SQLite** (see README §设计取舍): the dataset is tiny (dozens
to low hundreds of definitions), the file must be human-reviewable in a pull
request, and behaviour has to stay identical on every Python build without a
compiled extension. A lock file plus atomic replace is enough for the expected
single-writer-at-a-time workload; it is not a database and does not pretend to
be one.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .config import Config
from .errors import DuplicateMetricError, MetricNotFoundError, StorageError, ValidationError
from .models import SCHEMA_VERSION, MetricDefinition, clamp_confidence, utcnow_iso


class BaseMetricStore:
    """Common CRUD logic; subclasses only implement load / persist hooks."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config.from_env()
        self._metrics: Dict[str, MetricDefinition] = {}
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    # -- hooks --------------------------------------------------------------- #

    def _ensure_loaded(self) -> None:
        """Called before every read. No-op for in-memory storage."""

    def _persist(self) -> None:
        """Called after every mutation. No-op for in-memory storage."""

    def _mutate(self, operation):
        with self._lock:
            self._ensure_loaded()
            result = operation()
            self._persist()
            return result

    # -- reads ---------------------------------------------------------------- #

    def list_metrics(self) -> List[MetricDefinition]:
        with self._lock:
            self._ensure_loaded()
            return sorted(self._metrics.values(), key=lambda item: item.id)

    def count(self) -> int:
        return len(self.list_metrics())

    def has(self, metric_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return metric_id in self._metrics

    def get(self, metric_id: str) -> MetricDefinition:
        with self._lock:
            self._ensure_loaded()
            definition = self._metrics.get(metric_id)
            if definition is None:
                raise MetricNotFoundError(f"metric id not registered: {metric_id}")
            return definition

    def find_by_name(self, name: str) -> List[MetricDefinition]:
        needle = (name or "").strip().casefold()
        with self._lock:
            self._ensure_loaded()
            return [
                definition
                for definition in self._metrics.values()
                if definition.name.casefold() == needle
            ]

    def history(self, metric_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            entries = [
                dict(entry)
                for entry in self._history
                if metric_id is None or entry.get("metric_id") == metric_id
            ]
        return entries[-max(0, limit) :] if limit else entries

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return {
                "schema_version": SCHEMA_VERSION,
                "updated_at": utcnow_iso(),
                "metrics": {key: value.to_dict() for key, value in self._metrics.items()},
                "history": [dict(entry) for entry in self._history],
            }

    # -- writes --------------------------------------------------------------- #

    def upsert(self, definition: MetricDefinition, *, replace: bool = True) -> Tuple[MetricDefinition, bool]:
        """Insert or update. Returns ``(definition, created)``."""

        def operation() -> Tuple[MetricDefinition, bool]:
            existing = self._metrics.get(definition.id)
            if existing is not None and not replace:
                raise DuplicateMetricError(
                    f"metric id already exists: {definition.id} (pass replace=true to update)"
                )
            created = existing is None
            if existing is not None:
                definition.created_at = existing.created_at
                if not definition.stats.get("successes") and not definition.stats.get("failures"):
                    definition.stats = dict(existing.stats)
                definition.updated_at = utcnow_iso()
            self._metrics[definition.id] = definition
            return definition, created

        return self._mutate(operation)

    def bulk_load(self, definitions: Iterable[MetricDefinition], *, replace: bool = True) -> int:
        count = 0
        for definition in definitions:
            self.upsert(definition, replace=replace)
            count += 1
        return count

    def delete(self, metric_id: str) -> bool:
        def operation() -> bool:
            return self._metrics.pop(metric_id, None) is not None

        return self._mutate(operation)

    def apply_feedback(
        self,
        metric_id: str,
        *,
        old_confidence: float,
        new_confidence: float,
        signal: float,
        alpha: float,
        success: bool,
        note: str = "",
        source: str = "agent_feedback",
    ) -> MetricDefinition:
        """Persist a confidence change and its audit trail atomically."""

        def operation() -> MetricDefinition:
            definition = self._metrics.get(metric_id)
            if definition is None:
                raise MetricNotFoundError(f"metric id not registered: {metric_id}")
            definition.confidence = clamp_confidence(new_confidence)
            if success:
                definition.stats["successes"] = int(definition.stats.get("successes", 0)) + 1
            else:
                definition.stats["failures"] = int(definition.stats.get("failures", 0)) + 1
            definition.stats["last_feedback_at"] = utcnow_iso()
            definition.stats["last_signal"] = round(float(signal), 6)
            definition.updated_at = utcnow_iso()
            self._history.append(
                {
                    "metric_id": metric_id,
                    "old_confidence": round(float(old_confidence), 6),
                    "new_confidence": round(float(definition.confidence), 6),
                    "signal": round(float(signal), 6),
                    "alpha": round(float(alpha), 6),
                    "success": bool(success),
                    "note": note,
                    "source": source,
                    "ts": utcnow_iso(),
                }
            )
            if len(self._history) > self.config.max_history:
                self._history = self._history[-self.config.max_history :]
            return definition

        return self._mutate(operation)

    def clear(self) -> int:
        def operation() -> int:
            removed = len(self._metrics)
            self._metrics.clear()
            self._history.clear()
            return removed

        return self._mutate(operation)


class InMemoryMetricStore(BaseMetricStore):
    """Volatile store; ideal for tests and for runtimes without a writable disk."""

    volatile = True

    def __init__(
        self,
        definitions: Optional[Iterable[MetricDefinition]] = None,
        config: Optional[Config] = None,
    ) -> None:
        super().__init__(config)
        if definitions:
            for definition in definitions:
                self.upsert(definition)


class JsonMetricStore(BaseMetricStore):
    """File-backed store with atomic replaces and a cross-process lock file."""

    volatile = False

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        config: Optional[Config] = None,
        lock_timeout: float = 5.0,
        stale_lock_seconds: float = 30.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout = float(lock_timeout)
        self.stale_lock_seconds = float(stale_lock_seconds)
        self._signature: Optional[Tuple[int, int]] = None
        super().__init__(config)

    # -- helpers -------------------------------------------------------------- #

    def _file_signature(self) -> Optional[Tuple[int, int]]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _ensure_loaded(self) -> None:
        signature = self._file_signature()
        if signature == self._signature:
            # Same file (or still no file): the in-memory state is authoritative.
            # Comparing even the None/None case matters, because a reload while
            # the file does not exist yet would discard unpersisted writes.
            return
        self._load_from_disk(signature)

    def _document(self) -> Dict[str, Any]:
        """Serialise the current in-memory state WITHOUT reloading from disk.

        ``_persist`` must not call the public ``snapshot()``: snapshot refreshes
        from disk first, which would resurrect stale data (and, on the very
        first write, erase the mutation that is about to be written).
        """

        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": utcnow_iso(),
            "metrics": {key: value.to_dict() for key, value in self._metrics.items()},
            "history": [dict(entry) for entry in self._history],
        }

    def _load_from_disk(self, signature: Optional[Tuple[int, int]] = None) -> None:
        signature = signature if signature is not None else self._file_signature()
        if signature is None:
            # File absent: an empty workspace. Atomic replace never leaves the
            # path temporarily missing, so this means "no data yet".
            self._metrics = {}
            self._history = []
            self._signature = None
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"cannot read store {self.path}: {exc}") from exc
        try:
            document = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise StorageError(f"store {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise StorageError(f"store {self.path} must contain a JSON object")

        raw_metrics = document.get("metrics") or {}
        if not isinstance(raw_metrics, dict):
            raise StorageError(f"store {self.path}: 'metrics' must be an object")
        metrics: Dict[str, MetricDefinition] = {}
        for key, payload in raw_metrics.items():
            if not isinstance(payload, dict):
                raise StorageError(f"store {self.path}: metric {key!r} must be an object")
            enriched = dict(payload)
            enriched.setdefault("id", key)
            try:
                metrics[str(key)] = MetricDefinition.from_dict(enriched)
            except (ValidationError, TypeError) as exc:
                raise StorageError(f"store {self.path}: invalid metric {key!r}: {exc}") from exc

        raw_history = document.get("history") or []
        if not isinstance(raw_history, list):
            raise StorageError(f"store {self.path}: 'history' must be a list")

        self._metrics = metrics
        self._history = [entry for entry in raw_history if isinstance(entry, dict)][
            -self.config.max_history :
        ]
        self._signature = signature

    def _persist(self) -> None:
        document = self._document()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            raise StorageError(f"cannot write store {self.path}: {exc}") from exc
        self._signature = self._file_signature()

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout
        handle = None
        while True:
            try:
                handle = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(handle, str(os.getpid()).encode("ascii"))
                break
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > self.stale_lock_seconds:
                        self.lock_path.unlink()
                        continue
                except OSError:
                    continue
                if time.monotonic() >= deadline:
                    raise StorageError(
                        f"timed out after {self.lock_timeout}s waiting for store lock {self.lock_path}"
                    )
                time.sleep(0.02)
        try:
            yield
        finally:
            if handle is not None:
                try:
                    os.close(handle)
                except OSError:
                    pass
            try:
                self.lock_path.unlink()
            except OSError:
                pass

    def _mutate(self, operation):
        with self._lock, self._file_lock():
            self._load_from_disk()
            result = operation()
            self._persist()
            return result


def open_store(config: Optional[Config] = None, *, allow_volatile: Optional[bool] = None) -> BaseMetricStore:
    """Open the JSON store, degrading to memory when the path is unusable.

    Degrading is explicit (``store.volatile`` is True and the service reports a
    warning) instead of silently losing writes.
    """

    config = config or Config.from_env()
    allow_volatile = config.allow_volatile_store if allow_volatile is None else allow_volatile
    try:
        store = JsonMetricStore(config.store_path, config=config)
        store.list_metrics()  # force a load so that a corrupt file fails here
        return store
    except (StorageError, OSError) as exc:
        if not allow_volatile:
            raise
        fallback = InMemoryMetricStore(config=config)
        fallback.load_error = str(exc)  # type: ignore[attr-defined]
        return fallback
"""Runtime configuration, resolved from environment variables with defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict

ENV_PREFIX = "METRIC_SEMANTICS_"


def _env(name: str) -> str | None:
    value = os.environ.get(ENV_PREFIX + name)
    return value if value not in (None, "") else None


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def default_store_path() -> str:
    """``$METRIC_SEMANTICS_STORE`` or ``./data/metrics.json`` next to the project."""

    explicit = _env("STORE")
    if explicit:
        return explicit
    return str(Path(__file__).resolve().parent.parent / "data" / "metrics.json")


@dataclass(frozen=True)
class Config:
    """Thresholds are all overridable through ``METRIC_SEMANTICS_*`` env vars."""

    store_path: str = ""
    semantic_distance_threshold: float = 0.35
    numeric_penalty: float = 0.25
    confidence_human_threshold: float = 0.60
    ema_alpha: float = 0.20
    top_k: int = 5
    max_history: int = 500
    max_definition_chars: int = 600
    llm_timeout_seconds: float = 30.0
    allow_volatile_store: bool = True

    def __post_init__(self) -> None:
        for field_name in ("semantic_distance_threshold", "numeric_penalty", "confidence_human_threshold"):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be within [0, 1], got {value}")
        if not 0.0 < float(self.ema_alpha) <= 1.0:
            raise ValueError("ema_alpha must be within (0, 1]")
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
        if self.max_history < 1:
            raise ValueError("max_history must be >= 1")
        if self.max_definition_chars < 40:
            raise ValueError("max_definition_chars must be >= 40")
        if not self.store_path:
            object.__setattr__(self, "store_path", default_store_path())

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            store_path=default_store_path(),
            semantic_distance_threshold=_env_float("DISTANCE_THRESHOLD", 0.35),
            numeric_penalty=_env_float("NUMERIC_PENALTY", 0.25),
            confidence_human_threshold=_env_float("HUMAN_CONFIDENCE_THRESHOLD", 0.60),
            ema_alpha=_env_float("EMA_ALPHA", 0.20),
            top_k=_env_int("TOP_K", 5),
            max_history=_env_int("MAX_HISTORY", 500),
            max_definition_chars=_env_int("MAX_DEFINITION_CHARS", 600),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT", 30.0),
        )

    def with_overrides(self, **overrides: Any) -> "Config":
        allowed = {item.name for item in fields(self)}
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"unknown config overrides: {sorted(unknown)}")
        payload = {item.name: getattr(self, item.name) for item in fields(self)}
        payload.update(overrides)
        return Config(**payload)

    def to_dict(self) -> Dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}
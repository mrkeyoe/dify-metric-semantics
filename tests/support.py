"""Shared test helpers (standard library only)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from core.config import Config
from core.llm import StaticLLMClient
from core.seeds import example_definitions
from core.service import MetricSemanticsService
from core.storage import InMemoryMetricStore


def make_config(**overrides: Any) -> Config:
    base = dict(
        store_path=":memory:",
        semantic_distance_threshold=0.35,
        numeric_penalty=0.25,
        confidence_human_threshold=0.60,
        ema_alpha=0.20,
        top_k=5,
        max_history=50,
        max_definition_chars=600,
    )
    base.update(overrides)
    return Config(**base)


def make_service(*, llm: Optional[StaticLLMClient] = None, seed: bool = True, **config_overrides: Any):
    config = make_config(**config_overrides)
    store = InMemoryMetricStore(config=config)
    service = MetricSemanticsService(store=store, llm=llm, config=config)
    if seed:
        for definition in example_definitions():
            service.register_definition(definition)
    return service, store


def arbitration_json(**payload: Any) -> str:
    base: Dict[str, Any] = {
        "equivalent": False,
        "chosen_id": "active_user.30d_order",
        "rationale": "口径 A 统计 7 天登录行为，口径 B 统计 30 天支付订单，统计实体与窗口均不同。",
        "confidence": 0.78,
        "requires_human": False,
        "conflicts": [
            {
                "ids": ["active_user.7d_login", "active_user.30d_order"],
                "type": "semantic",
                "detail": "时间窗口 + 统计实体不同",
            }
        ],
    }
    base.update(payload)
    return json.dumps(base, ensure_ascii=False)
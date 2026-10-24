"""Service factory shared by the Dify tools and the CLI.

Credential handling rule: nothing is hard-coded. Model credentials come from the
Dify provider configuration (``self.runtime.credentials``) or from the
``METRIC_SEMANTICS_LLM_*`` environment variables.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .config import Config
from .errors import LLMUnavailable
from .llm import LLMClient, HttpChatClient, llm_from_env
from .service import MetricSemanticsService
from .storage import BaseMetricStore, open_store


def _credential(credentials: Optional[Mapping[str, Any]], key: str) -> str:
    if not credentials:
        return ""
    value = credentials.get(key)
    return str(value).strip() if value is not None else ""


def build_llm(
    credentials: Optional[Mapping[str, Any]] = None,
    *,
    config: Optional[Config] = None,
) -> Optional[LLMClient]:
    config = config or Config.from_env()
    base = _credential(credentials, "model_api_base")
    key = _credential(credentials, "model_api_key")
    model = _credential(credentials, "model_name")
    if base and key and model:
        try:
            return HttpChatClient(base, key, model, timeout=config.llm_timeout_seconds)
        except LLMUnavailable:
            return None
    return llm_from_env(config.llm_timeout_seconds)


def build_service(
    *,
    credentials: Optional[Mapping[str, Any]] = None,
    config: Optional[Config] = None,
    store: Optional[BaseMetricStore] = None,
    llm: Optional[LLMClient] = None,
    allow_volatile: Optional[bool] = None,
) -> MetricSemanticsService:
    config = config or Config.from_env()
    store_path = _credential(credentials, "store_path")
    if store_path:
        config = config.with_overrides(store_path=store_path)

    if store is None:
        store = open_store(config, allow_volatile=allow_volatile)
    if llm is None:
        llm = build_llm(credentials, config=config)
    return MetricSemanticsService(store=store, llm=llm, config=config)
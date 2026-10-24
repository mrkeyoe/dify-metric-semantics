"""Shared wiring for the four Dify tools.

The tools are deliberately thin: parse nothing, decide nothing — they hand the
parameter dict to ``core.tool_handlers`` and turn the JSON payload into Dify
messages. All business logic stays in the unit-tested ``core`` package.
"""

from __future__ import annotations

from typing import Any

from adapters.dify_model import dify_session_llm
from core.config import Config
from core.factory import build_llm, build_service
from core.llm import LLMClient
from core.service import MetricSemanticsService


def credentials_of(tool: Any) -> dict:
    runtime = getattr(tool, "runtime", None)
    credentials = getattr(runtime, "credentials", None)
    return dict(credentials) if isinstance(credentials, dict) else {}


def build_llm_for_tool(tool: Any, credentials: dict, config: Config) -> LLMClient | None:
    """Prefer explicit model credentials, then the Dify session gateway.

    Nothing is hard-coded: ``build_llm`` only returns a client when the provider
    configuration (or ``METRIC_SEMANTICS_LLM_*``) supplies base URL + key +
    model. Otherwise we fall back to Dify's own model gateway, and if that is
    unavailable too the arbitration degrades gracefully (``requires_human``).
    """

    llm = build_llm(credentials, config=config)
    if llm is not None:
        return llm
    return dify_session_llm(
        getattr(tool, "session", None),
        credentials,
        timeout=config.llm_timeout_seconds,
    )


def service_for_tool(tool: Any) -> MetricSemanticsService:
    credentials = credentials_of(tool)
    config = Config.from_env()
    llm = build_llm_for_tool(tool, credentials, config)
    return build_service(credentials=credentials, config=config, llm=llm)
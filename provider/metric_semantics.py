"""Dify tool provider for metric semantics."""

from __future__ import annotations

from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from adapters.dify_model import dify_session_llm
from core.config import Config
from core.factory import build_llm, build_service


class MetricSemanticsProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        """Fail fast when the configured store cannot be opened.

        Model credentials are optional on purpose: without them the tools still
        work (resolve / record_outcome / list_metrics) and `arbitrate` degrades
        to `requires_human=true` instead of failing the whole provider.
        """

        credentials = dict(credentials or {})
        try:
            config = Config.from_env()
            llm = build_llm(credentials, config=config)
            if llm is None:
                llm = dify_session_llm(getattr(self, "session", None), credentials)
            service = build_service(credentials=credentials, config=config, llm=llm)
            health = service.health()
        except Exception as exc:  # credential problems must be actionable, not a traceback
            raise ToolProviderCredentialValidationError(str(exc)) from exc

        if health.get("store_error"):
            raise ToolProviderCredentialValidationError(
                "存储不可用，已回退到内存（重启会丢失）：" + str(health["store_error"])
            )
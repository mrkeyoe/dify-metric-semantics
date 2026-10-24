"""Use the Dify runtime's model session as the arbitration LLM.

The plugin must never hold an API key of its own: Dify already owns the model
credentials and exposes them through the tool's ``session.model.llm`` gateway.
This adapter wraps that gateway in the tiny ``core.llm.LLMClient`` protocol.

The ``dify_plugin`` SDK is imported *inside* the call, so importing this module
(and running the whole unit test suite) works without the SDK installed. If the
gateway is missing or its API shape differs from what this adapter expects, we
raise ``LLMUnavailable`` and the arbitration degrades to a rule-based fallback
instead of crashing the tool.

NOTE (honest limitation): the exact SDK call shape below has NOT been verified
against a running Dify instance on this machine. See README "已知限制".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from core.errors import LLMError, LLMUnavailable
from core.llm import LLMClient


def dify_sdk_available() -> bool:
    """True when the Dify plugin SDK can be imported in this interpreter."""

    try:
        import dify_plugin  # noqa: F401
    except Exception:
        return False
    return True


def _load_sdk():
    """Import the pieces of the SDK the adapter needs, or raise LLMUnavailable."""

    try:
        from dify_plugin.entities.model import ModelRequest, ModelType  # type: ignore
        from dify_plugin.entities.model.message import (  # type: ignore
            SystemPromptMessage,
            UserPromptMessage,
        )
    except Exception as exc:  # ImportError, or a renamed module in a newer SDK
        raise LLMUnavailable(
            "dify_plugin SDK 不可用或 API 不匹配：无法调用 Dify 模型网关。"
            "核心逻辑不依赖它；如需 LLM 仲裁，请在插件运行环境中提供 "
            "model_api_base / model_api_key / model_name 凭据，或退回离线 stub。"
            f"（{type(exc).__name__}: {exc}）"
        ) from exc
    return ModelRequest, ModelType, SystemPromptMessage, UserPromptMessage


def _content_of(response: Any) -> str:
    """Best-effort extraction of the assistant text from an SDK response."""

    if isinstance(response, str):
        return response
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    for attribute in ("content", "text", "answer"):
        value = getattr(response, attribute, None)
        if isinstance(value, str):
            return value
    if isinstance(response, dict):
        for key in ("content", "text", "answer"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    raise LLMError(f"cannot extract text from model response: {type(response).__name__}")


@dataclass
class DifyModelClient:
    """``LLMClient`` backed by ``session.model.llm`` from the Dify runtime."""

    session: Any
    model_name: str = ""
    model_provider: str = ""
    timeout: float = 30.0
    temperature: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        model = self.model_name or "default"
        return f"dify-session-model:{model}"

    # -- gateway plumbing ---------------------------------------------------- #

    def _gateway(self) -> Any:
        session = self.session
        if session is None:
            raise LLMUnavailable("Dify session 不存在：工具没有拿到 session.model 网关。")
        model = getattr(session, "model", None)
        llm = getattr(model, "llm", None) if model is not None else None
        if llm is None or not hasattr(llm, "invoke"):
            raise LLMUnavailable("Dify session.model.llm.invoke 不可用（SDK 版本不匹配？）。")
        return llm

    def _invoke(self, gateway: Any, system: str, prompt: str, max_tokens: int) -> Any:
        ModelRequest, ModelType, SystemPromptMessage, UserPromptMessage = _load_sdk()
        from dify_plugin.entities.model import ModelConfig  # type: ignore

        config = ModelConfig(provider=self.model_provider, model=self.model_name)
        messages = []
        if system:
            messages.append(SystemPromptMessage(content=system))
        messages.append(UserPromptMessage(content=prompt))

        attempts: List[Tuple[str, Any]] = [
            (
                "ModelRequest",
                lambda: ModelRequest(
                    model_config=config,
                    prompt_messages=messages,
                    model_type=ModelType.LLM,
                    stream=False,
                ),
            ),
        ]

        errors: List[str] = []
        for label, build in attempts:
            try:
                request = build()
            except TypeError as exc:
                errors.append(f"{label}: {exc}")
                continue
            try:
                return gateway.invoke(request)
            except TypeError as exc:
                # Some SDK builds take keyword arguments directly.
                errors.append(f"{label}.invoke: {exc}")
                return gateway.invoke(
                    model_config=config,
                    prompt_messages=messages,
                    model_type=ModelType.LLM,
                    stream=False,
                )
        raise LLMUnavailable("Dify 模型网关调用参数不匹配：" + "; ".join(errors))

    # -- LLMClient protocol -------------------------------------------------- #

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:
        gateway = self._gateway()
        try:
            response = self._invoke(gateway, system, prompt, max_tokens)
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMError(f"Dify 模型调用失败：{type(exc).__name__}: {exc}") from exc
        return _content_of(response)


def dify_session_llm(
    session: Any,
    credentials: Optional[dict] = None,
    *,
    timeout: float = 30.0,
) -> Optional[DifyModelClient]:
    """Return a session-backed client, or ``None`` when no gateway is present."""

    if session is None:
        return None
    credentials = credentials or {}
    return DifyModelClient(
        session=session,
        model_name=str(credentials.get("model_name") or "").strip(),
        model_provider=str(credentials.get("model_provider") or "").strip(),
        timeout=timeout,
    )


def as_llm_client(client: Optional[DifyModelClient]) -> Optional[LLMClient]:
    return client if client is not None else None
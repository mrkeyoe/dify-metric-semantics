"""Model gateway abstractions.

``core`` never hard-codes an API key. Credentials arrive from the Dify provider
configuration (``model_api_base`` / ``model_api_key`` / ``model_name``) or from
``METRIC_SEMANTICS_LLM_*`` environment variables.

Only the standard library is used here (``urllib``), which keeps the whole
decision path testable without any third-party package.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from .errors import LLMError, LLMUnavailable

DEFAULT_SYSTEM_PROMPT = ""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal contract the arbitration engine needs from a model."""

    name: str

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:  # pragma: no cover - protocol declaration
        ...


class StaticLLMClient:
    """Deterministic client for tests and offline demos.

    ``responses`` may be a single string, a list of strings/exception instances,
    or a callable receiving ``(prompt, system)``.
    """

    name = "static"

    def __init__(self, responses: Any = None) -> None:
        self._responses = responses
        self.calls: List[Dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if callable(self._responses):
            return str(self._responses(prompt, system))

        queue = self._responses
        if isinstance(queue, str):
            return queue
        if isinstance(queue, BaseException):
            raise queue
        if isinstance(queue, (list, tuple)):
            if not queue:
                raise LLMError("StaticLLMClient has no queued response left")
            item = queue[0] if len(queue) == 1 else queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return str(item)
        if queue is None:
            raise LLMError("StaticLLMClient was created without responses")
        return str(queue)


class HttpChatClient:
    """OpenAI-compatible ``/chat/completions`` client (urllib, no dependencies)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise LLMUnavailable("model_api_base is empty")
        if not api_key:
            raise LLMUnavailable("model_api_key is empty")
        if not model:
            raise LLMUnavailable("model_name is empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.name = f"http:{model}"

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"cannot reach model endpoint: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError(f"model call timed out after {self.timeout}s") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model endpoint returned non-JSON: {payload[:300]!r}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected model response shape: {str(data)[:300]!r}") from exc
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError("model returned empty content")
        return content


def llm_from_env(config_timeout: float = 30.0) -> Optional[LLMClient]:
    """Build an HTTP client from ``METRIC_SEMANTICS_LLM_*`` when all parts exist."""

    base = os.environ.get("METRIC_SEMANTICS_LLM_BASE", "").strip()
    key = os.environ.get("METRIC_SEMANTICS_LLM_KEY", "").strip()
    model = os.environ.get("METRIC_SEMANTICS_LLM_MODEL", "").strip()
    if base and key and model:
        return HttpChatClient(base, key, model, timeout=config_timeout)
    return None


def describe_client(client: Optional[LLMClient]) -> str:
    if client is None:
        return "none"
    return getattr(client, "name", type(client).__name__)
"""Thin adapters between the pure ``core`` package and the Dify runtime.

Everything under ``core`` is standard-library only and unit tested. The modules
in this package are the only place that touches Dify SDK objects, and they
import the SDK lazily so the repository still imports and tests cleanly on a
machine where ``dify_plugin`` is not installed.
"""

from .dify_model import DifyModelClient, dify_sdk_available, dify_session_llm

__all__ = ["DifyModelClient", "dify_sdk_available", "dify_session_llm"]
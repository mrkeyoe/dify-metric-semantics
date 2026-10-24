"""Dify tool: resolve_metric — find registered calibers for a metric mention."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.tool_handlers import dispatch
from tools._shared import service_for_tool


class ResolveMetricTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        service = service_for_tool(self)
        payload = dispatch("resolve_metric", service, dict(tool_parameters or {}))

        yield self.create_json_message(payload)
        if payload.get("ok"):
            summary = payload.get("summary") or f"matched {payload.get('candidate_count', 0)} candidate(s)"
            if payload.get("conflict"):
                summary = f"口径冲突：{summary}"
            yield self.create_text_message(str(summary))
        else:
            yield self.create_text_message(str(payload.get("message", "resolve_metric failed")))
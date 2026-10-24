"""Dify tool: list_metrics — discovery helper for the agent."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.tool_handlers import dispatch
from tools._shared import service_for_tool


class ListMetricsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        service = service_for_tool(self)
        payload = dispatch("list_metrics", service, dict(tool_parameters or {}))

        if payload.get("ok"):
            names = ", ".join(item.get("id", "") for item in payload.get("metrics", [])[:10])
            yield self.create_text_message(f"已注册指标 {payload.get('count', 0)} 个：{names}")
        else:
            yield self.create_text_message(str(payload.get("message", "list_metrics failed")))
        yield self.create_json_message(payload)
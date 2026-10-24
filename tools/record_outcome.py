"""Dify tool: record_outcome — learning loop (EMA confidence update)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.tool_handlers import dispatch
from tools._shared import service_for_tool


class RecordOutcomeTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        service = service_for_tool(self)
        payload = dispatch("record_outcome", service, dict(tool_parameters or {}))

        if payload.get("ok"):
            text = (
                f"口径 {payload.get('metric_id')} 的 confidence 已更新："
                f"{payload.get('old_confidence')} → {payload.get('new_confidence')}"
                f"（signal={payload.get('signal')}, alpha={payload.get('alpha')}）"
            )
            yield self.create_text_message(text)
        else:
            yield self.create_text_message(str(payload.get("message", "record_outcome failed")))
        yield self.create_json_message(payload)
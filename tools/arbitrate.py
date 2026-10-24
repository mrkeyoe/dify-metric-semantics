"""Dify tool: arbitrate — ask the model which caliber should be used."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.tool_handlers import dispatch
from tools._shared import service_for_tool


class ArbitrateTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        service = service_for_tool(self)
        payload = dispatch("arbitrate", service, dict(tool_parameters or {}))

        if payload.get("ok"):
            if payload.get("resolved"):
                text = (
                    f"建议采用口径 {payload.get('chosen_name') or payload.get('chosen_id')}"
                    f"（confidence={payload.get('confidence')}）：{payload.get('rationale')}"
                )
            else:
                text = (
                    "无法自动定论，需人工确认（requires_human=true）。"
                    f"模型理由：{payload.get('rationale') or payload.get('error') or '未提供'}"
                )
            yield self.create_text_message(text)
        else:
            yield self.create_text_message(str(payload.get("message", "arbitrate failed")))
        yield self.create_json_message(payload)
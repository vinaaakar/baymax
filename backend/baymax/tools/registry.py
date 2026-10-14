"""Registry of available tools."""

from __future__ import annotations

from .base import Tool
from .catalog import meta_for


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if not tool.name:
            raise ValueError("tool must have a name")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        meta = meta_for(tool.name)
        if meta is not None:
            for attr in ("intents", "permissions", "timeout_s", "verification", "recovery"):
                if not getattr(type(tool), attr, None):
                    setattr(tool, attr, getattr(meta, attr))
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]

    def model_tools(self) -> list[Tool]:
        return [t for t in self.all() if t.model_callable]

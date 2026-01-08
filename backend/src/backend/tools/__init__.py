from __future__ import annotations

from google.adk.tools import FunctionTool

from ..deps import Deps
from ..settings import Settings
from .export import build_export_tool
from .sql import build_preview_schema_tool, build_sql_tool

CLIENT_TOOL_NAMES = frozenset({"export_csv"})


def build_tools(deps: Deps, settings: Settings) -> list[FunctionTool]:
    tools: list[FunctionTool] = []
    tools.append(build_preview_schema_tool(deps))
    tools.append(build_sql_tool(deps, settings))
    tools.append(build_export_tool(deps))
    return tools

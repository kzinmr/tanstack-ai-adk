"""
ADK agent definition for the SQL analysis demo.
"""

from __future__ import annotations

from datetime import date

from google.adk.agents import LlmAgent
from google.adk.apps.app import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from .db import DB_SCHEMA, SQL_EXAMPLES
from .deps import Deps
from .settings import Settings
from .tools import build_tools


def _format_as_xml(examples: list[dict[str, str]]) -> str:
    lines = ["<examples>"]
    for example in examples:
        lines.append("  <example>")
        lines.append(f"    <request>{example['request']}</request>")
        lines.append(f"    <response>{example['response']}</response>")
        lines.append("  </example>")
    lines.append("</examples>")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return f"""\
You are a helpful data analyst assistant. Your job is to help users analyze
log data stored in a PostgreSQL database.

## Database Schema

{DB_SCHEMA}

## Today's Date

{date.today()}

## Important Rules

1. **Safety First**: Only SELECT queries are allowed. Never modify data.
2. **Always Use LIMIT**: Every query must include LIMIT to prevent large result sets.
3. **Approval Flow**: execute_sql and export_csv require approval. When you're ready
   to run them, call the tool directly; the system will request approval and pause
   execution. Do not ask for approval in plain text or wait for a manual "approve"
   response. You may include a brief explanation alongside the tool call.
4. **Use Artifact IDs**: After executing SQL, results are stored with an artifact_id.
   The UI will automatically preview results from artifacts. Do not show
   artifact_id to the user directly. Tool results include a JSON payload with
   artifacts[].id for internal use.
5. **CSV Export**: When the user wants to download data as CSV, use export_csv.
   This also requires approval and runs on the client side.

## Workflow Example

1. User asks to analyze error logs from yesterday
2. You write a SQL query and call execute_sql (system requests approval)
3. After approval, the query runs and results are stored as an artifact_id
4. The UI will show a preview of the data (do not mention artifact_id to the user)
5. If user wants to download, call export_csv (system requests approval + client execution)

## SQL Examples

{_format_as_xml(SQL_EXAMPLES)}
"""


def create_runner(
    *, deps: Deps, settings: Settings, session_service: BaseSessionService | None = None
) -> Runner:
    tools = build_tools(deps, settings)
    agent = LlmAgent(
        name="sql_agent",
        model=settings.llm_model,
        description="SQL analysis assistant",
        instruction=build_system_prompt(),
        tools=tools,
    )
    app = App(
        name=settings.adk_app_name,
        root_agent=agent,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    if session_service is None:
        session_service = InMemorySessionService()
    return Runner(app=app, session_service=session_service)

"""TanStack model message input conversion to ADK types."""

from __future__ import annotations

from typing import Any

from google.genai import types


def extract_user_text(messages: list[dict[str, Any]]) -> str | None:
    """Return the most recent user text content from TanStack model messages."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if isinstance(content, list):
            parts = [
                part.get("content", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            text = "".join(parts).strip()
            return text or None
    return None


def build_user_content(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def build_function_response_content(
    responses: list[types.FunctionResponse],
) -> types.Content:
    parts = [types.Part(function_response=response) for response in responses]
    return types.Content(role="user", parts=parts)

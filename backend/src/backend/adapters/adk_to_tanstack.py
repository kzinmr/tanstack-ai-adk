"""Translate ADK events into TanStack AI StreamChunks."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from google.adk.events.event import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.genai import types

from ..domain.models import PendingAction
from ..ports import RunStorePort
from ..tools import CLIENT_TOOL_NAMES
from .tanstack_stream import (
    ApprovalObj,
    ApprovalRequestedStreamChunk,
    ContentStreamChunk,
    ErrorObj,
    ErrorStreamChunk,
    ToolCall,
    ToolCallFunction,
    ToolCallStreamChunk,
    ToolInputAvailableStreamChunk,
    ToolResultStreamChunk,
    now_ms,
)
from .tanstack_to_adk import build_function_response_content, build_user_content


class TanStackAdkAdapter:
    def __init__(
        self,
        *,
        run_id: str,
        model: str,
        runner: Runner,
        run_store: RunStorePort,
        user_id: str,
    ) -> None:
        self.run_id = run_id
        self.model = model
        self.runner = runner
        self.run_store = run_store
        self.user_id = user_id
        self._text_accumulator = ""
        self._tool_call_index = 0

    async def run_from_user_text(self, text: str) -> AsyncIterator[object]:
        self.run_store.get_or_create(self.run_id)
        await self._ensure_session()
        content = build_user_content(text)
        async for chunk in self._run_with_content(new_message=content):
            yield chunk

    async def resume_from_continuation(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[object]:
        self.run_store.get_or_create(self.run_id)
        approvals = payload.get("approvals") or {}
        tool_results = payload.get("tool_results") or {}

        approval_responses_by_invocation: dict[str, list[types.FunctionResponse]] = {}
        tool_responses_by_invocation: dict[str, list[types.FunctionResponse]] = {}
        client_tool_chunks: list[ToolInputAvailableStreamChunk] = []

        for tool_call_id, approved in approvals.items():
            pending = self.run_store.pop_pending_approval(self.run_id, tool_call_id)
            if pending is None:
                continue
            confirmation_id = pending.adk_confirmation_call_id
            if not confirmation_id:
                continue
            response = types.FunctionResponse(
                id=confirmation_id,
                name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                response={"confirmed": bool(approved)},
            )
            approval_responses_by_invocation.setdefault(
                pending.invocation_id, []
            ).append(response)

            if pending.tool_name in CLIENT_TOOL_NAMES:
                if approved:
                    client_tool_chunks.append(
                        ToolInputAvailableStreamChunk(
                            id=self.run_id,
                            model=self.model,
                            timestamp=now_ms(),
                            toolCallId=pending.tool_call_id,
                            toolName=pending.tool_name,
                            input=pending.tool_input,
                        )
                    )
                else:
                    self.run_store.pop_pending_client_tool(
                        self.run_id, pending.tool_call_id
                    )

        for tool_call_id, result in tool_results.items():
            pending = self.run_store.pop_pending_client_tool(self.run_id, tool_call_id)
            if pending is None:
                continue
            payload = result
            if isinstance(result, dict) and "output" in result:
                payload = result["output"]
            response = types.FunctionResponse(
                id=tool_call_id,
                name=pending.tool_name,
                response={"output": payload},
            )
            tool_responses_by_invocation.setdefault(
                pending.invocation_id, []
            ).append(response)

        for chunk in client_tool_chunks:
            yield chunk

        for invocation_id, responses in approval_responses_by_invocation.items():
            async for chunk in self._run_with_content(
                invocation_id=invocation_id,
                new_message=build_function_response_content(responses),
            ):
                yield chunk

        for invocation_id, responses in tool_responses_by_invocation.items():
            async for chunk in self._run_with_content(
                invocation_id=invocation_id,
                new_message=build_function_response_content(responses),
            ):
                yield chunk

    def has_pending(self) -> bool:
        return self.run_store.has_pending(self.run_id)

    async def _ensure_session(self) -> None:
        session = await self.runner.session_service.get_session(
            app_name=self.runner.app_name,
            user_id=self.user_id,
            session_id=self.run_id,
        )
        if session is not None:
            return
        await self.runner.session_service.create_session(
            app_name=self.runner.app_name,
            user_id=self.user_id,
            session_id=self.run_id,
        )

    async def _run_with_content(
        self,
        *,
        invocation_id: str | None = None,
        new_message: types.Content | None,
    ) -> AsyncIterator[object]:
        if new_message is None:
            return
        try:
            async for event in self.runner.run_async(
                user_id=self.user_id,
                session_id=self.run_id,
                invocation_id=invocation_id,
                new_message=new_message,
            ):
                async for chunk in self._event_to_chunks(event):
                    yield chunk
        except Exception as exc:
            yield ErrorStreamChunk(
                id=self.run_id,
                model=self.model,
                timestamp=now_ms(),
                error=ErrorObj(message=str(exc)),
            )

    async def _event_to_chunks(self, event: Event) -> AsyncIterator[object]:
        if event.invocation_id:
            self.run_store.set_invocation_id(self.run_id, event.invocation_id)

        content = event.content
        if not content or not content.parts:
            return

        for part in content.parts:
            if part.text:
                delta = part.text
                if event.partial is False or event.partial is None:
                    if delta.startswith(self._text_accumulator):
                        delta = delta[len(self._text_accumulator) :]
                        self._text_accumulator += delta
                    else:
                        self._text_accumulator += delta
                else:
                    self._text_accumulator += delta
                yield ContentStreamChunk(
                    id=self.run_id,
                    model=self.model,
                    timestamp=now_ms(),
                    content=self._text_accumulator,
                    delta=delta,
                    role="assistant",
                )

            if part.function_call:
                async for chunk in self._handle_function_call(
                    event, part.function_call
                ):
                    yield chunk

            if part.function_response:
                async for chunk in self._handle_function_response(
                    event, part.function_response
                ):
                    yield chunk

    async def _handle_function_call(
        self, event: Event, function_call: types.FunctionCall
    ) -> AsyncIterator[object]:
        if function_call.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
            async for chunk in self._handle_confirmation_call(event, function_call):
                yield chunk
            return

        tool_call_id = function_call.id or self._generate_tool_call_id()
        tool_input = function_call.args or {}

        if function_call.name in CLIENT_TOOL_NAMES:
            self.run_store.add_pending_client_tool(
                self.run_id,
                PendingAction(
                    kind="client_tool",
                    tool_call_id=tool_call_id,
                    tool_name=function_call.name or "unknown",
                    tool_input=tool_input,
                    invocation_id=event.invocation_id,
                ),
            )

        arguments = json.dumps(tool_input, ensure_ascii=False)
        yield ToolCallStreamChunk(
            id=self.run_id,
            model=self.model,
            timestamp=now_ms(),
            index=self._next_tool_call_index(),
            toolCall=ToolCall(
                id=tool_call_id,
                function=ToolCallFunction(
                    name=function_call.name or "",
                    arguments=arguments,
                ),
            ),
        )

    async def _handle_confirmation_call(
        self, event: Event, function_call: types.FunctionCall
    ) -> AsyncIterator[object]:
        args = function_call.args or {}
        original_call = args.get("originalFunctionCall") or {}
        tool_call_id = original_call.get("id")
        tool_name = original_call.get("name")
        tool_input = original_call.get("args")

        if not tool_call_id or not tool_name:
            return

        self.run_store.add_pending_approval(
            self.run_id,
            PendingAction(
                kind="approval",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_input=tool_input,
                invocation_id=event.invocation_id,
                adk_confirmation_call_id=function_call.id,
            ),
        )

        yield ApprovalRequestedStreamChunk(
            id=self.run_id,
            model=self.model,
            timestamp=now_ms(),
            toolCallId=tool_call_id,
            toolName=tool_name,
            input=tool_input,
            approval=ApprovalObj(id=tool_call_id),
        )

    async def _handle_function_response(
        self, event: Event, function_response: types.FunctionResponse
    ) -> AsyncIterator[object]:
        if function_response.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
            return

        if (
            event.actions.requested_tool_confirmations
            and function_response.id in event.actions.requested_tool_confirmations
        ):
            return

        content = self._extract_tool_result_content(function_response.response)
        if content is None:
            return

        yield ToolResultStreamChunk(
            id=self.run_id,
            model=self.model,
            timestamp=now_ms(),
            toolCallId=function_response.id or "",
            content=content,
        )

    def _extract_tool_result_content(self, response: dict[str, Any] | None) -> str | None:
        if response is None:
            return None
        value: Any
        if "output" in response:
            value = response["output"]
        elif "result" in response:
            value = response["result"]
        elif "response" in response:
            value = response["response"]
        else:
            value = response

        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _generate_tool_call_id(self) -> str:
        return f"tool_{uuid.uuid4().hex}"

    def _next_tool_call_index(self) -> int:
        value = self._tool_call_index
        self._tool_call_index += 1
        return value

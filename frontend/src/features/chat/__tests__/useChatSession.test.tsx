// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamChunk, UIMessage } from "@tanstack/ai";
import { useChatSession } from "../hooks/useChatSession";

let mockMessages: UIMessage[] = [];
let mockIsLoading = false;
let mockError: Error | null = null;
const sendMessage = vi.fn();
const setMessages = vi.fn();

let capturedOnChunk: ((chunk: StreamChunk) => void) | null = null;

vi.mock("@tanstack/ai-react", () => ({
  useChat: (opts: { onChunk?: (chunk: StreamChunk) => void }) => {
    capturedOnChunk = opts?.onChunk ?? null;
    return {
      messages: mockMessages,
      sendMessage,
      setMessages,
      isLoading: mockIsLoading,
      error: mockError,
    };
  },
}));

vi.mock("../chatConnection", () => ({
  createChatConnection: () => ({ connect: vi.fn() }),
}));

function makeDoneChunk(
  runId: string,
  finishReason: "tool_calls" | "stop" = "tool_calls"
): StreamChunk {
  return {
    type: "done",
    id: runId,
    model: "test-model",
    timestamp: 0,
    finishReason,
  };
}

function makeApprovalChunk(runId: string): StreamChunk {
  return {
    type: "approval-requested",
    id: runId,
    model: "test-model",
    timestamp: 0,
    toolCallId: "call-1",
    toolName: "execute_sql",
    input: { sql: "SELECT 1" },
    approval: { id: "call-1", needsApproval: true },
  };
}

function makeToolCallMessage(): UIMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    parts: [
      {
        type: "tool-call",
        id: "call-1",
        name: "execute_sql",
        arguments: "{\"sql\":\"SELECT 1\"}",
        state: "input-complete",
      },
    ],
  };
}

beforeEach(() => {
  mockMessages = [];
  mockIsLoading = false;
  mockError = null;
  sendMessage.mockReset();
  setMessages.mockReset();
  capturedOnChunk = null;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
});

describe("useChatSession", () => {
  it("exposes pending approvals when approval-requested chunk arrives", () => {
    mockMessages = [makeToolCallMessage()];
    const { result } = renderHook(() => useChatSession());

    act(() => {
      capturedOnChunk?.(makeApprovalChunk("run-1"));
    });

    expect(result.current.pendingApprovals).toHaveLength(1);
    expect(result.current.pendingApprovals[0].toolCallId).toBe("call-1");
    expect(result.current.pendingApprovals[0].input).toEqual({
      sql: "SELECT 1",
    });
  });

  it("posts approvals to /api/continuation on approve", async () => {
    mockMessages = [makeToolCallMessage()];
    const { result } = renderHook(() => useChatSession());

    act(() => {
      capturedOnChunk?.(makeApprovalChunk("run-1"));
    });

    await act(async () => {
      await result.current.approve("call-1");
    });

    expect(fetch).toHaveBeenCalledWith("/api/continuation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: "run-1", approvals: { "call-1": true } }),
    });
  });

  it("posts tool results to /api/continuation on resolve", async () => {
    const { result } = renderHook(() => useChatSession());

    act(() => {
      capturedOnChunk?.(makeDoneChunk("run-1", "stop"));
    });

    await act(async () => {
      await result.current.resolveClientTool("tool-1", "export_csv", {
        output: { success: true },
        state: "output-available",
      });
    });

    expect(fetch).toHaveBeenCalledWith("/api/continuation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: "run-1",
        tool_results: {
          "tool-1": {
            tool: "export_csv",
            output: { success: true },
            state: "output-available",
            errorText: undefined,
          },
        },
      }),
    });
  });

  it("sets pending client tool when tool-input-available arrives", () => {
    const { result } = renderHook(() => useChatSession());

    act(() => {
      capturedOnChunk?.({
        type: "tool-input-available",
        id: "run-1",
        model: "test-model",
        timestamp: 0,
        toolCallId: "call-2",
        toolName: "export_csv",
        input: { artifact_id: "a_run-1_1" },
      });
    });

    expect(result.current.pendingClientTool).toEqual({
      toolCallId: "call-2",
      toolName: "export_csv",
      input: { artifact_id: "a_run-1_1" },
      runId: "run-1",
    });
  });
});

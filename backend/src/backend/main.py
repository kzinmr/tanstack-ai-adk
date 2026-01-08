"""
FastAPI application for the TanStack AI HITL Demo (ADK backend).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from structlog.contextvars import bound_contextvars
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from .adapters.adk_to_tanstack import TanStackAdkAdapter
from .adapters.tanstack_stream import DoneStreamChunk, encode_chunk, encode_done, now_ms
from .adapters.tanstack_to_adk import extract_user_text
from .agents.sql_agent.agent import create_runner
from .continuation import ContinuationHub
from .db import get_db_connection
from .deps import Deps
from .logging import configure_logging, get_logger
from .settings import get_settings
from .store import get_artifact_store, get_run_store

# Get settings
settings = get_settings()
configure_logging()
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="TanStack AI HITL Demo",
    description="SQL Analysis Agent with Human-in-the-Loop",
    version="0.1.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Run store for HITL continuation (swap via settings)
store = get_run_store()
continuation_hub = ContinuationHub()
session_service = InMemorySessionService()


def _sse_headers() -> dict[str, str]:
    """Standard SSE response headers."""
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }


@app.post("/api/continuation")
async def continuation(request: Request) -> JSONResponse:
    body = await request.json()
    run_id = body.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="Missing run_id")
    continuation_hub.push(run_id, body)
    return JSONResponse({"status": "ok"})


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    import json

    body = await request.body()
    try:
        body_json = json.loads(body) if body else {}
    except json.JSONDecodeError:
        body_json = {}

    run_id = body_json.get("run_id") or body_json.get("data", {}).get("run_id")
    if not run_id:
        run_id = uuid.uuid4().hex

    messages = body_json.get("messages") if isinstance(body_json, dict) else []
    user_text = extract_user_text(messages or [])

    async def stream() -> AsyncIterator[bytes]:
        with bound_contextvars(run_id=run_id):
            async with get_db_connection() as conn:
                deps = Deps(
                    conn=conn,
                    run_id=run_id,
                    artifact_store=get_artifact_store(),
                )
                runner = create_runner(
                    deps=deps,
                    settings=settings,
                    session_service=session_service,
                )
                adapter = TanStackAdkAdapter(
                    run_id=run_id,
                    model=settings.llm_model,
                    runner=runner,
                    run_store=store,
                    user_id=settings.adk_user_id,
                )

                if not user_text:
                    yield encode_chunk(
                        DoneStreamChunk(
                            id=run_id,
                            model=settings.llm_model,
                            timestamp=now_ms(),
                            finishReason="stop",
                        )
                    ).encode("utf-8")
                    yield encode_done().encode("utf-8")
                    return

                async for chunk in adapter.run_from_user_text(user_text):
                    yield encode_chunk(chunk).encode("utf-8")

                while adapter.has_pending():
                    payload = await continuation_hub.wait(run_id)
                    async for chunk in adapter.resume_from_continuation(payload):
                        yield encode_chunk(chunk).encode("utf-8")

                yield encode_chunk(
                    DoneStreamChunk(
                        id=run_id,
                        model=settings.llm_model,
                        timestamp=now_ms(),
                        finishReason="stop",
                    )
                ).encode("utf-8")
                yield encode_done().encode("utf-8")

    return StreamingResponse(
        stream(),
        headers=_sse_headers(),
    )


@app.get("/api/data/{run_id}/{artifact_id:path}")
async def get_csv_data(
    run_id: str,
    artifact_id: str,
    mode: str = Query(default="preview", pattern="^(preview|download)$"),
) -> dict:
    """
    Get CSV export data by run_id and artifact ID.

    This endpoint is called by the frontend after receiving a tool-input-available
    chunk for the export_csv tool. The artifact_id is included in the tool args,
    and the run_id is used to scope the data. Use mode=download to return a
    signed URL when the artifact store supports it.

    Args:
        run_id: The run ID that produced this dataset
        artifact_id: The artifact identifier

    Returns:
        JSON with rows/columns or a signed download URL
    """
    artifact_store = get_artifact_store()

    if mode == "download":
        download = artifact_store.get_download(run_id, artifact_id)
        if download is not None:
            return {
                "mode": "signed-url",
                "download_url": download.url,
                "expires_in_seconds": download.expires_in_seconds,
                "method": download.method,
                "headers": download.headers,
            }

    preview = artifact_store.get_preview(run_id, artifact_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Artifact not found or expired")

    return {
        "mode": "inline",
        "rows": preview.rows,
        "columns": preview.columns,
        "original_row_count": preview.original_row_count,
        "exported_row_count": preview.exported_row_count,
    }


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "model": settings.llm_model,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

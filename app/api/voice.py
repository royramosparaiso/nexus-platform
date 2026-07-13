"""WebSocket voice endpoint — /_voice/stream.

Client protocol:
  1. Client opens WS to /_voice/stream
  2. Client sends {"text": "...", "voice": "af_bella", "speed": 1.0} (JSON)
  3. Server sends {"event": "start", "voice": "..."} (JSON)
  4. Server sends N binary frames (PCM chunks)
  5. Server sends {"event": "end", "chunks": N, "bytes": B} (JSON) and closes

Error events:
  - {"event": "unavailable"}          — backend not configured (KOKORO_URL missing)
  - {"event": "error", "detail": ...} — backend transport error
  - {"event": "rejected", "reason":...} — client payload invalid

The client can preempt at any time by sending {"event": "cancel"} — we tear
down the backend stream and close cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from httpx import HTTPError

from app.services.voice import (
    KokoroUnavailable,
    SynthesisRequest,
    control_frame,
    kokoro_base_url,
    stream_from_backend,
)


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/_voice/health")
async def voice_health() -> dict:
    """Report whether a Kokoro backend is configured (does NOT ping it)."""
    return {
        "configured": kokoro_base_url() is not None,
        "backend_url": kokoro_base_url(),
    }


@router.websocket("/_voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    await ws.accept()
    try:
        raw = await ws.receive_text()
    except WebSocketDisconnect:
        return

    try:
        payload = json.loads(raw)
        req = SynthesisRequest.from_json(payload)
    except (json.JSONDecodeError, ValueError) as e:
        await ws.send_text(control_frame("rejected", reason=str(e)))
        await ws.close(code=1003)
        return

    await ws.send_text(control_frame("start", voice=req.voice, format=req.response_format))

    chunks = 0
    total_bytes = 0
    try:
        cancelled = False
        stream_task: asyncio.Task[None] | None = None

        async def _pump() -> None:
            nonlocal chunks, total_bytes
            async for chunk in stream_from_backend(req):
                if cancelled:
                    return
                await ws.send_bytes(chunk)
                chunks += 1
                total_bytes += len(chunk)

        # Concurrently watch for cancel frame while pumping audio.
        stream_task = asyncio.create_task(_pump())

        async def _watch_cancel() -> None:
            nonlocal cancelled
            try:
                while not stream_task.done():
                    msg = await ws.receive_text()
                    if json.loads(msg).get("event") == "cancel":
                        cancelled = True
                        stream_task.cancel()
                        return
            except (WebSocketDisconnect, json.JSONDecodeError, asyncio.CancelledError):
                cancelled = True
                stream_task.cancel()

        watcher = asyncio.create_task(_watch_cancel())
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
        watcher.cancel()

    except KokoroUnavailable:
        await ws.send_text(control_frame("unavailable"))
        await ws.close(code=1011)
        return
    except HTTPError as e:
        logger.warning("voice backend HTTP error: %s", e)
        await ws.send_text(control_frame("error", detail=f"backend: {type(e).__name__}"))
        await ws.close(code=1011)
        return

    await ws.send_text(control_frame("end", chunks=chunks, bytes=total_bytes))
    await ws.close(code=1000)

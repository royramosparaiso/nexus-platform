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
    DEFAULT_VOICE,
    KokoroUnavailable,
    SynthesisRequest,
    control_frame,
    kokoro_base_url,
    list_backend_voices,
    stream_from_backend,
)


logger = logging.getLogger(__name__)
router = APIRouter()


# Curated fallback catalogue — used when the backend is unreachable or has
# not yet exposed its voice list. Kokoro voice ids follow ``{lang}{gender}_``
# prefixes; we decode them here so the UI can group / filter without doing
# string surgery client-side.
_LANG_PREFIX = {
    "a": ("en-US", "American English"),
    "b": ("en-GB", "British English"),
    "e": ("es", "Spanish"),
    "f": ("fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),
    "p": ("pt", "Portuguese"),
    "z": ("zh", "Chinese"),
}
_GENDER_PREFIX = {"f": "female", "m": "male"}

_FALLBACK_VOICES = [
    "af_bella", "af_heart", "af_sky", "af_nicole", "af_sarah",
    "am_adam", "am_michael", "am_eric",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
    "ef_dora", "em_alex",
    "jf_alpha", "jm_kumo",
]


def _describe_voice(voice_id: str) -> dict:
    prefix = voice_id[:2] if len(voice_id) >= 3 and voice_id[2] == "_" else ""
    lang_code, lang_label = _LANG_PREFIX.get(prefix[:1], (None, None))
    gender = _GENDER_PREFIX.get(prefix[1:2]) if len(prefix) == 2 else None
    return {
        "id": voice_id,
        "language": lang_code,
        "language_label": lang_label,
        "gender": gender,
    }


@router.get("/_voice/health")
async def voice_health() -> dict:
    """Report whether a Kokoro backend is configured (does NOT ping it)."""
    return {
        "configured": kokoro_base_url() is not None,
        "backend_url": kokoro_base_url(),
    }


@router.get("/_voice/voices")
async def voice_catalogue() -> dict:
    """List voices available for /_voice/stream.

    Queries the Kokoro backend when configured; falls back to a curated
    static catalogue otherwise so the cockpit can still render controls.
    The response also carries ``default`` and ``source`` so clients know
    whether the list is live or cached defaults.
    """
    source = "backend"
    voice_ids: list[str]
    try:
        voice_ids = await list_backend_voices()
        if not voice_ids:
            voice_ids = list(_FALLBACK_VOICES)
            source = "fallback-empty"
    except KokoroUnavailable:
        voice_ids = list(_FALLBACK_VOICES)
        source = "fallback-unconfigured"
    except HTTPError as e:
        logger.warning("voice catalogue backend error: %s", e)
        voice_ids = list(_FALLBACK_VOICES)
        source = "fallback-error"

    return {
        "source": source,
        "default": DEFAULT_VOICE,
        "voices": [_describe_voice(v) for v in voice_ids],
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

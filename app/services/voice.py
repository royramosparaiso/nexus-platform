"""Voice services — Kokoro TTS backend + WebSocket streaming layer.

Design decisions (recorded here so we don't re-litigate them):

* **Backend**: Kokoro-FastAPI (ghcr.io/remsky/kokoro-fastapi-cpu:latest). Runs
  as a sibling container inside the Nexus compose stack. Apache-2.0 licensed,
  82M-parameter model, no cloud dependency, no voice cloning risk.

* **Transport**: WebSocket end-to-end for the Nexus API surface, because we
  want future upgrades (LiveKit Agents, cloud TTS providers) to be drop-in
  without breaking clients. Kokoro-FastAPI does not speak WebSocket natively,
  so the Platform acts as a WS↔HTTP-chunked adapter: reads a request from the
  socket, opens an HTTP stream to the backend, forwards each PCM chunk to the
  socket, then closes with a terminal `{"event": "end"}` frame.

* **Contract**: sent frames are JSON (control), received frames are either
  JSON (control) or binary (audio). This lets the client stop early with
  `{"event": "cancel"}` and receive `{"event": "chunk_meta", "seq": N}`
  metadata alongside binary bytes.

* **Fallback**: if the backend is unreachable (no KOKORO_URL env var, no
  container running), the WS handshake succeeds but the first frame the
  server sends is `{"event": "unavailable"}` so the client can degrade
  gracefully to text-only mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


DEFAULT_VOICE = "af_bella"
DEFAULT_FORMAT = "pcm"          # raw 24kHz mono int16, cheapest to stream
DEFAULT_SPEED = 1.0
DEFAULT_MODEL = "kokoro"


def kokoro_base_url() -> str | None:
    return os.environ.get("KOKORO_URL")


@dataclass
class SynthesisRequest:
    text: str
    voice: str = DEFAULT_VOICE
    speed: float = DEFAULT_SPEED
    response_format: str = DEFAULT_FORMAT

    @classmethod
    def from_json(cls, raw: dict) -> "SynthesisRequest":
        text = str(raw.get("text", "")).strip()
        if not text:
            raise ValueError("text is required")
        if len(text) > 4000:
            raise ValueError("text too long (max 4000 chars)")
        return cls(
            text=text,
            voice=str(raw.get("voice", DEFAULT_VOICE)),
            speed=float(raw.get("speed", DEFAULT_SPEED)),
            response_format=str(raw.get("format", DEFAULT_FORMAT)),
        )

    def to_backend_payload(self) -> dict:
        return {
            "model": DEFAULT_MODEL,
            "input": self.text,
            "voice": self.voice,
            "response_format": self.response_format,
            "speed": self.speed,
        }


async def stream_from_backend(
    req: SynthesisRequest,
    *,
    base_url: str | None = None,
    chunk_size: int = 4096,
) -> AsyncIterator[bytes]:
    """Open an HTTP stream to Kokoro-FastAPI, yield raw audio chunks.

    Raises `KokoroUnavailable` if the backend URL is not configured, and
    `httpx.HTTPError` subclasses on transport / status errors.
    """
    url = base_url or kokoro_base_url()
    if not url:
        raise KokoroUnavailable("KOKORO_URL not configured")

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{url.rstrip('/')}/v1/audio/speech",
            json=req.to_backend_payload(),
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size):
                if chunk:
                    yield chunk


class KokoroUnavailable(RuntimeError):
    """Backend not configured or unreachable — caller decides how to degrade."""


async def list_backend_voices(
    *,
    base_url: str | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """Fetch the list of voices from the Kokoro-FastAPI backend.

    Returns a plain list of voice ids (e.g. ``["af_bella", "am_adam", ...]``).
    Raises ``KokoroUnavailable`` when no backend URL is configured, and
    ``httpx.HTTPError`` subclasses on transport / status errors.

    Kokoro-FastAPI responds with either ``{"voices": ["..."]}`` (canonical)
    or ``{"voices": [{"id": "..."}]}`` in newer builds — we normalise both
    into a flat list of ids so the caller never has to care.
    """
    url = base_url or kokoro_base_url()
    if not url:
        raise KokoroUnavailable("KOKORO_URL not configured")

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{url.rstrip('/')}/v1/audio/voices")
        resp.raise_for_status()
        data = resp.json()

    raw = data.get("voices", []) if isinstance(data, dict) else []
    ids: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            ids.append(entry)
        elif isinstance(entry, dict):
            vid = entry.get("id") or entry.get("name")
            if isinstance(vid, str) and vid:
                ids.append(vid)
    return ids


def control_frame(event: str, **extra) -> str:
    return json.dumps({"event": event, **extra})

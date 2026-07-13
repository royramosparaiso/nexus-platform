"""FastAPI entrypoint for Nexus Platform.

Endpoints (see docs/protocol-v0.6.md in nexus-core):
- POST /_bootstrap   — one-time handshake with Console
- POST /_commands    — signed JWT command from Console
- GET  /_health      — liveness
- GET  /_status      — instance status (requires JWT)
- GET  /_voice/health — whether Kokoro backend is configured
- WS   /_voice/stream — streaming text-to-speech (see app/api/voice.py)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.bootstrap import router as bootstrap_router
from app.api.commands import router as commands_router
from app.api.health import router as health_router
from app.api.status import router as status_router
from app.api.voice import router as voice_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Placeholder for future agent scheduler startup.
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nexus Platform",
        version=__version__,
        lifespan=_lifespan,
    )
    app.include_router(health_router)
    app.include_router(bootstrap_router)
    app.include_router(commands_router)
    app.include_router(status_router)
    app.include_router(voice_router)
    return app


app = create_app()

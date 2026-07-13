"""Liveness endpoint — no auth."""

from fastapi import APIRouter

from app import __version__

router = APIRouter()


@router.get("/_health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}

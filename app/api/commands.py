"""POST /_commands — signed command envelope from Console.

Verifies the JWT with the Console public key pinned at bootstrap. Dispatches
the command to the appropriate handler. Returns a CommandResult.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_core.contracts.commands import (
    CommandEnvelope, CommandResult, CommandStatus,
)
from nexus_core.jwt import ExpiredToken, InvalidSignature, verify_token

from app.db import get_db
from app.models import InstanceRow
from app.services.command_handlers import dispatch

router = APIRouter()


@router.post("/_commands", response_model=CommandResult)
async def commands(request: Request, db: AsyncSession = Depends(get_db)) -> CommandResult:
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    token = body.decode() if isinstance(body, bytes) else str(body)

    instance = (await db.execute(select(InstanceRow))).scalar_one_or_none()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="platform not bootstrapped",
        )

    try:
        payload = verify_token(token, instance.console_public_key_pem)
    except ExpiredToken:
        return CommandResult(
            cmd_id=_extract_cmd_id(token, fallback=instance.id),
            status=CommandStatus.REJECTED,
            error_code="expired",
        )
    except InvalidSignature:
        return CommandResult(
            cmd_id=_extract_cmd_id(token, fallback=instance.id),
            status=CommandStatus.REJECTED,
            error_code="invalid_signature",
        )

    envelope = CommandEnvelope.model_validate(payload)

    # v0.7 — dispatcher applies the command synchronously against Platform DB.
    # Long-running handlers (e.g. agent runtime, image pulls) return APPLIED
    # after recording an intent; the actual execution happens out-of-band.
    return await dispatch(db, envelope, instance)


def _extract_cmd_id(token: str, fallback):
    # Best-effort — pull cmd_id from JWT payload without verifying signature.
    # Used only to report failure back with a matching cmd_id when possible.
    import base64
    import json
    try:
        parts = token.split(".")
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw))
        return payload.get("cmd_id", fallback)
    except Exception:
        return fallback

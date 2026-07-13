"""GET /_status — full instance snapshot.

v0.6: not JWT-verified yet (marked TODO); returns the instance row plus its
spaces and areas.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import InstanceRow, SpaceRow

router = APIRouter()


@router.get("/_status")
async def get_status(db: AsyncSession = Depends(get_db)) -> dict:
    instance = (await db.execute(select(InstanceRow))).scalar_one_or_none()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="platform not bootstrapped",
        )

    spaces = (
        await db.execute(
            select(SpaceRow).options(selectinload(SpaceRow.areas)),
        )
    ).scalars().all()

    return {
        "instance_id": str(instance.id),
        "name": instance.name,
        "persona_kind": instance.persona_kind,
        "modality": instance.modality,
        "agent_runtime": instance.agent_runtime,
        "auth_provider": instance.auth_provider,
        "status": instance.status,
        "bootstrapped_at": instance.bootstrapped_at.isoformat(),
        "spaces": [
            {
                "id": str(s.id),
                "slug": s.slug,
                "name": s.name,
                "kind": s.kind,
                "is_personal": s.is_personal,
                "areas": [
                    {"slug": a.slug, "label": a.label, "tier": a.tier, "enabled": a.enabled}
                    for a in s.areas
                ],
            }
            for s in spaces
        ],
    }

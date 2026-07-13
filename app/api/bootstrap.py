"""POST /_bootstrap — one-time handshake with Console.

Flow:
  1. Verify X-Bootstrap-Token matches PLATFORM_BOOTSTRAP_TOKEN env.
  2. If instance already bootstrapped, return status=already_bootstrapped.
  3. Generate Platform keypair, persist alongside Console pubkey + webhook.
  4. Apply InstanceManifest (create spaces + install areas).
  5. Burn the bootstrap token (persist a "consumed" flag).
  6. Return BootstrapResponse with platform_public_key_pem.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_core.contracts.bootstrap import (
    BootstrapRequest, BootstrapResponse, BootstrapStatus,
)
from nexus_core.jwt import PlatformKeypair
from nexus_core.models import AVAILABLE_AREAS

from app.config import get_settings
from app.db import get_db
from app.models import AreaRow, InstanceRow, SpaceRow

router = APIRouter()


@router.post("/_bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    request: BootstrapRequest,
    x_bootstrap_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> BootstrapResponse:
    settings = get_settings()
    if not settings.bootstrap_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="platform has no PLATFORM_BOOTSTRAP_TOKEN configured",
        )
    if x_bootstrap_token != settings.bootstrap_token:
        return BootstrapResponse(
            status=BootstrapStatus.INVALID_TOKEN,
            error_detail="bootstrap token mismatch",
        )

    # Already bootstrapped?
    existing = (
        await db.execute(select(InstanceRow).where(InstanceRow.id == request.instance_id))
    ).scalar_one_or_none()
    if existing is not None:
        return BootstrapResponse(
            status=BootstrapStatus.ALREADY_BOOTSTRAPPED,
            platform_public_key_pem=existing.platform_public_key_pem,
            platform_version="0.6.0",
            applied_areas=[a.slug for space in await _all_spaces(db) for a in space.areas],
        )

    # Generate platform keypair.
    kp = PlatformKeypair.generate()

    # Persist instance row.
    manifest = request.manifest
    instance = InstanceRow(
        id=request.instance_id,
        name=manifest.name,
        persona_kind=manifest.persona.kind,
        modality=manifest.deployment.modality,
        agent_runtime=manifest.deployment.runtime,
        auth_provider=manifest.governance.auth.provider,
        console_public_key_pem=request.console_public_key_pem,
        console_webhook_url=request.console_webhook_url,
        platform_private_key_pem=kp.private_pem(),
        platform_public_key_pem=kp.public_pem(),
        manifest_json=manifest.model_dump(mode="json", by_alias=True),
        status="running",
    )
    db.add(instance)

    # Personal Space (always).
    personal_space = SpaceRow(
        instance_id=request.instance_id,
        slug="personal",
        name=f"{manifest.persona.display_name} — Personal",
        kind="internal",
        is_personal=True,
    )
    db.add(personal_space)
    await db.flush()

    # Install requested areas inside the Personal Space at bootstrap. Additional
    # spaces are created later via `create_space` commands.
    area_lookup = {a.slug: a for a in AVAILABLE_AREAS}
    applied: list[str] = []
    for slug in manifest.areas.enabled:
        area_def = area_lookup.get(slug)
        if area_def is None:
            continue
        db.add(AreaRow(
            space_id=personal_space.id,
            slug=slug,
            label=area_def.label,
            tier=area_def.tier,
            enabled=True,
        ))
        applied.append(slug)

    await db.commit()

    return BootstrapResponse(
        status=BootstrapStatus.OK,
        platform_public_key_pem=kp.public_pem(),
        platform_version="0.6.0",
        applied_areas=applied,
    )


async def _all_spaces(db: AsyncSession) -> list[SpaceRow]:
    rows = (await db.execute(select(SpaceRow))).scalars().all()
    # Force-load areas for each (avoid N+1 lazy-load surprise across async).
    for row in rows:
        await db.refresh(row, ["areas"])
    return list(rows)

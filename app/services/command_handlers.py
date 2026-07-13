"""Command dispatcher — routes CommandEnvelope → handler → CommandResult.

Handlers mutate Platform state (Spaces, Areas, Agents, Users, LLM providers).
Each handler is a coroutine that takes (db, envelope, instance) and returns
a CommandResult. Handlers are pure w.r.t. Platform DB: they never call out
to Console (Console is notified via the webhook queue in app/services/notify.py).

Registration is explicit — no reflection tricks — so `grep CommandKind.X` is
enough to find the code path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_core.contracts.commands import (
    CommandEnvelope,
    CommandKind,
    CommandResult,
    CommandStatus,
)
from nexus_core.models import AVAILABLE_AREAS

from app.models import AreaRow, AuditRow, InstanceRow, SpaceRow


Handler = Callable[
    [AsyncSession, CommandEnvelope, InstanceRow],
    Awaitable[CommandResult],
]

_REGISTRY: dict[CommandKind, Handler] = {}


def register(kind: CommandKind):
    def _decorator(fn: Handler) -> Handler:
        _REGISTRY[kind] = fn
        return fn
    return _decorator


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _applied(envelope: CommandEnvelope, detail: str) -> CommandResult:
    return CommandResult(
        cmd_id=envelope.cmd_id,
        status=CommandStatus.APPLIED,
        detail=detail,
        applied_at=_now_epoch(),
    )


def _failed(envelope: CommandEnvelope, error_code: str, detail: str) -> CommandResult:
    return CommandResult(
        cmd_id=envelope.cmd_id,
        status=CommandStatus.FAILED,
        error_code=error_code,
        detail=detail,
    )


async def _audit(
    db: AsyncSession, actor: str, event_kind: str, payload: dict,
) -> None:
    db.add(AuditRow(actor=actor, event_kind=event_kind, payload=payload))


# ────────────────────────────────────────────────────────────────
# Space lifecycle
# ────────────────────────────────────────────────────────────────

@register(CommandKind.CREATE_SPACE)
async def _create_space(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    slug = p.get("slug")
    name = p.get("name")
    kind = p.get("kind", "internal")
    if not slug or not name:
        return _failed(envelope, "invalid_payload", "slug and name required")

    exists = (await db.execute(
        select(SpaceRow).where(
            SpaceRow.instance_id == instance.id, SpaceRow.slug == slug,
        )
    )).scalar_one_or_none()
    if exists is not None:
        return _failed(envelope, "conflict", f"space '{slug}' already exists")

    space = SpaceRow(
        instance_id=instance.id, slug=slug, name=name, kind=kind,
        is_personal=False,
    )
    db.add(space)
    await _audit(db, actor="console", event_kind="space.created", payload={
        "space_slug": slug, "space_name": name, "kind": kind,
    })
    await db.commit()
    return _applied(envelope, f"space '{slug}' created")


@register(CommandKind.DELETE_SPACE)
async def _delete_space(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    slug = p.get("slug")
    if not slug:
        return _failed(envelope, "invalid_payload", "slug required")

    space = (await db.execute(
        select(SpaceRow).where(
            SpaceRow.instance_id == instance.id, SpaceRow.slug == slug,
        )
    )).scalar_one_or_none()
    if space is None:
        return _failed(envelope, "not_found", f"space '{slug}' not found")
    if space.is_personal:
        return _failed(envelope, "forbidden", "cannot delete personal space")

    await db.delete(space)
    await _audit(db, actor="console", event_kind="space.deleted", payload={
        "space_slug": slug,
    })
    await db.commit()
    return _applied(envelope, f"space '{slug}' deleted")


# ────────────────────────────────────────────────────────────────
# Area lifecycle
# ────────────────────────────────────────────────────────────────

def _resolve_space(
    db: AsyncSession, instance: InstanceRow, payload: dict,
) -> Awaitable[SpaceRow | None]:
    """Accept either `space_id` (UUID) or `space_slug` (str)."""
    raw_id = payload.get("space_id")
    slug = payload.get("space_slug")
    if raw_id is not None:
        try:
            space_id = UUID(str(raw_id))
        except ValueError:
            async def _bad():
                return None
            return _bad()
        return _by_id(db, instance, space_id)
    if slug:
        return _by_slug(db, instance, slug)
    async def _none():
        return None
    return _none()


async def _by_id(db, instance, sid):
    return (await db.execute(
        select(SpaceRow).where(
            SpaceRow.instance_id == instance.id, SpaceRow.id == sid,
        )
    )).scalar_one_or_none()


async def _by_slug(db, instance, slug):
    return (await db.execute(
        select(SpaceRow).where(
            SpaceRow.instance_id == instance.id, SpaceRow.slug == slug,
        )
    )).scalar_one_or_none()


@register(CommandKind.INSTALL_AREA)
async def _install_area(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    area_slug = p.get("area_slug")
    if not area_slug:
        return _failed(envelope, "invalid_payload", "area_slug required")

    space = await _resolve_space(db, instance, p)
    if space is None:
        return _failed(envelope, "not_found", "space not found")

    area_def = next((a for a in AVAILABLE_AREAS if a.slug == area_slug), None)
    if area_def is None:
        return _failed(envelope, "unknown_area", f"area '{area_slug}' unknown")

    existing = (await db.execute(
        select(AreaRow).where(
            AreaRow.space_id == space.id, AreaRow.slug == area_slug,
        )
    )).scalar_one_or_none()

    if existing is not None:
        if existing.enabled:
            return _failed(envelope, "conflict", f"area '{area_slug}' already installed")
        existing.enabled = True
        await _audit(db, actor="console", event_kind="area.reenabled", payload={
            "space_slug": space.slug, "area_slug": area_slug,
        })
        await db.commit()
        return _applied(envelope, f"area '{area_slug}' re-enabled")

    db.add(AreaRow(
        space_id=space.id, slug=area_slug, label=area_def.label,
        tier=area_def.tier, enabled=True,
    ))
    await _audit(db, actor="console", event_kind="area.installed", payload={
        "space_slug": space.slug, "area_slug": area_slug, "tier": area_def.tier,
    })
    await db.commit()
    return _applied(envelope, f"area '{area_slug}' installed in space '{space.slug}'")


@register(CommandKind.UNINSTALL_AREA)
async def _uninstall_area(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    area_slug = p.get("area_slug")
    if not area_slug:
        return _failed(envelope, "invalid_payload", "area_slug required")

    space = await _resolve_space(db, instance, p)
    if space is None:
        return _failed(envelope, "not_found", "space not found")

    area = (await db.execute(
        select(AreaRow).where(
            AreaRow.space_id == space.id, AreaRow.slug == area_slug,
        )
    )).scalar_one_or_none()
    if area is None or not area.enabled:
        return _failed(envelope, "not_found", f"area '{area_slug}' not installed")

    area.enabled = False
    await _audit(db, actor="console", event_kind="area.uninstalled", payload={
        "space_slug": space.slug, "area_slug": area_slug,
    })
    await db.commit()
    return _applied(envelope, f"area '{area_slug}' disabled in space '{space.slug}'")


# ────────────────────────────────────────────────────────────────
# Agent lifecycle (v0.7 metadata-only — real runtime lands with in-process worker)
# ────────────────────────────────────────────────────────────────

@register(CommandKind.DEPLOY_AGENT)
async def _deploy_agent(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    """Register an agent deployment intent in the audit log.

    v0.7 does not yet run agents — this handler records the intent so Console
    (and later the in-process runtime) can pick it up. The runtime PR will
    add an `agent` table and a worker loop.
    """
    p = envelope.command.payload
    agent_slug = p.get("agent_slug")
    area_slug = p.get("area_slug")
    if not agent_slug or not area_slug:
        return _failed(envelope, "invalid_payload", "agent_slug and area_slug required")
    space = await _resolve_space(db, instance, p)
    if space is None:
        return _failed(envelope, "not_found", "space not found")

    await _audit(db, actor="console", event_kind="agent.deploy_requested", payload={
        "space_slug": space.slug, "area_slug": area_slug,
        "agent_slug": agent_slug, "config": p.get("config", {}),
    })
    await db.commit()
    return _applied(envelope, f"deploy of '{agent_slug}' recorded (runtime PR pending)")


@register(CommandKind.KILL_SWITCH_AGENT)
async def _kill_switch_agent(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    agent_slug = p.get("agent_slug")
    if not agent_slug:
        return _failed(envelope, "invalid_payload", "agent_slug required")
    await _audit(db, actor="console", event_kind="agent.kill_switch", payload={
        "agent_slug": agent_slug, "reason": p.get("reason", "unspecified"),
    })
    await db.commit()
    return _applied(envelope, f"kill switch fired for '{agent_slug}'")


# ────────────────────────────────────────────────────────────────
# LLM providers
# ────────────────────────────────────────────────────────────────

@register(CommandKind.SET_LLM_PROVIDER)
async def _set_llm_provider(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    """Rewrite the manifest.llms.roles entry for a given role."""
    p = envelope.command.payload
    role = p.get("role")
    model = p.get("model")
    if role not in {"planner", "coordinator", "worker", "embeddings"}:
        return _failed(envelope, "invalid_payload", f"unknown role '{role}'")
    if not model:
        return _failed(envelope, "invalid_payload", "model required")

    manifest = dict(instance.manifest_json)
    llms = dict(manifest.get("llms", {}))
    roles = dict(llms.get("roles", {}))
    roles[role] = model
    llms["roles"] = roles
    manifest["llms"] = llms
    instance.manifest_json = manifest

    await _audit(db, actor="console", event_kind="llm.role_updated", payload={
        "role": role, "model": model,
    })
    await db.commit()
    return _applied(envelope, f"role '{role}' set to '{model}'")


@register(CommandKind.ROTATE_SECRET)
async def _rotate_secret(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    p = envelope.command.payload
    key = p.get("key")
    if not key:
        return _failed(envelope, "invalid_payload", "key required")
    # Actual secret rotation happens out-of-band (compose env var swap +
    # container restart). This handler just records the rotation intent.
    await _audit(db, actor="console", event_kind="secret.rotated", payload={
        "key": key,
    })
    await db.commit()
    return _applied(envelope, f"secret '{key}' rotation recorded")


# ────────────────────────────────────────────────────────────────
# Platform lifecycle
# ────────────────────────────────────────────────────────────────

@register(CommandKind.PAUSE)
async def _pause(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    if instance.status == "paused":
        return _failed(envelope, "conflict", "already paused")
    instance.status = "paused"
    await _audit(db, actor="console", event_kind="platform.paused", payload={})
    await db.commit()
    return _applied(envelope, "platform paused")


@register(CommandKind.RESUME)
async def _resume(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    if instance.status == "running":
        return _failed(envelope, "conflict", "already running")
    instance.status = "running"
    await _audit(db, actor="console", event_kind="platform.resumed", payload={})
    await db.commit()
    return _applied(envelope, "platform resumed")


@register(CommandKind.UPGRADE_PLATFORM)
async def _upgrade_platform(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    """Record an upgrade intent. The actual image swap is done by the deployer
    outside the Platform (compose pull + up), because a running Platform can't
    upgrade itself in-process."""
    p = envelope.command.payload
    target = p.get("target_version")
    if not target:
        return _failed(envelope, "invalid_payload", "target_version required")
    await _audit(db, actor="console", event_kind="platform.upgrade_requested", payload={
        "from": "0.7.0", "to": target,
    })
    await db.commit()
    return _applied(envelope, f"upgrade to {target} recorded — deployer must pull+up")


@register(CommandKind.GRANT_CROSS_INSTANCE_ACCESS)
async def _grant_cross_instance(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    """v0.7 records the grant; enforcement lands with the cross-instance auth PR."""
    p = envelope.command.payload
    peer = p.get("peer_instance_id")
    scopes = p.get("scopes", [])
    if not peer or not scopes:
        return _failed(envelope, "invalid_payload", "peer_instance_id and scopes required")
    await _audit(db, actor="console", event_kind="cross_instance.granted", payload={
        "peer_instance_id": peer, "scopes": scopes,
    })
    await db.commit()
    return _applied(envelope, f"cross-instance access granted to {peer} for {len(scopes)} scopes")


# ────────────────────────────────────────────────────────────────
# Dispatch
# ────────────────────────────────────────────────────────────────

async def dispatch(
    db: AsyncSession, envelope: CommandEnvelope, instance: InstanceRow,
) -> CommandResult:
    """Route an envelope to its handler and return the result."""
    handler = _REGISTRY.get(envelope.command.kind)
    if handler is None:
        return CommandResult(
            cmd_id=envelope.cmd_id,
            status=CommandStatus.REJECTED,
            error_code="unhandled_kind",
            detail=f"no handler for '{envelope.command.kind.value}'",
        )
    try:
        return await handler(db, envelope, instance)
    except Exception as e:  # noqa: BLE001 — surface unknown errors as FAILED
        await db.rollback()
        return CommandResult(
            cmd_id=envelope.cmd_id,
            status=CommandStatus.FAILED,
            error_code="handler_exception",
            detail=f"{type(e).__name__}: {e}",
        )


def registered_kinds() -> set[CommandKind]:
    """Introspection helper used by tests + /_status."""
    return set(_REGISTRY.keys())

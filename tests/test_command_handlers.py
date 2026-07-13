"""Handlers coverage for /_commands dispatcher."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from nexus_core.contracts.bootstrap import BootstrapRequest
from nexus_core.contracts.commands import Command, CommandEnvelope, CommandKind
from nexus_core.jwt import ConsoleKeypair, sign_command
from nexus_core.models import (
    AreasConfig, AuthConfig, DeploymentConfig, GovernanceConfig,
    InstanceManifest, LlmConfig, LlmRoleAssignment, MemoryConfig, PersonaConfig,
)


def _manifest():
    return InstanceManifest(
        name="handlers-test",
        persona=PersonaConfig(display_name="R", kind="personal"),
        deployment=DeploymentConfig(modality="local", runtime="in_process"),
        llms=LlmConfig(
            enabled_providers=["ollama"],
            roles=LlmRoleAssignment(
                planner="p", coordinator="c", worker="w", embeddings="e",
            ),
        ),
        memory=MemoryConfig(driver="sqlite"),
        areas=AreasConfig(enabled=["personal_organization"]),
        governance=GovernanceConfig(auth=AuthConfig(provider="password_totp")),
    )


@pytest.fixture
async def bootstrapped(client):
    kp = ConsoleKeypair.generate()
    iid = uuid4()
    req = BootstrapRequest(
        instance_id=iid,
        console_public_key_pem=kp.public_pem(),
        console_webhook_url="http://console.test/cb",
        manifest=_manifest(),
    )
    await client.post(
        "/_bootstrap",
        json=req.model_dump(mode="json"),
        headers={"X-Bootstrap-Token": "test-token"},
    )
    return {"client": client, "kp": kp, "iid": iid}


async def _send(ctx, kind: CommandKind, payload: dict):
    now = int(time.time())
    env = CommandEnvelope(
        instance_id=ctx["iid"], issued_at=now, expires_at=now + 300,
        command=Command(kind=kind, payload=payload),
    )
    token = sign_command(ctx["kp"], env)
    r = await ctx["client"].post(
        "/_commands", content=token, headers={"Content-Type": "application/jwt"},
    )
    return r.json()


# ────────────────────────────────────────────────────────────────
# Space
# ────────────────────────────────────────────────────────────────

async def test_create_space_applied(bootstrapped):
    r = await _send(bootstrapped, CommandKind.CREATE_SPACE, {
        "slug": "family", "name": "Familia Ramos", "kind": "internal",
    })
    assert r["status"] == "applied", r
    st = (await bootstrapped["client"].get("/_status")).json()
    slugs = {s["slug"] for s in st["spaces"]}
    assert slugs == {"personal", "family"}


async def test_create_space_conflict(bootstrapped):
    await _send(bootstrapped, CommandKind.CREATE_SPACE, {
        "slug": "acme", "name": "Acme",
    })
    r = await _send(bootstrapped, CommandKind.CREATE_SPACE, {
        "slug": "acme", "name": "Acme 2",
    })
    assert r["status"] == "failed"
    assert r["error_code"] == "conflict"


async def test_create_space_invalid_payload(bootstrapped):
    r = await _send(bootstrapped, CommandKind.CREATE_SPACE, {"slug": "x"})
    assert r["status"] == "failed"
    assert r["error_code"] == "invalid_payload"


async def test_delete_space_ok(bootstrapped):
    await _send(bootstrapped, CommandKind.CREATE_SPACE, {
        "slug": "temp", "name": "Temp",
    })
    r = await _send(bootstrapped, CommandKind.DELETE_SPACE, {"slug": "temp"})
    assert r["status"] == "applied"


async def test_delete_personal_space_forbidden(bootstrapped):
    r = await _send(bootstrapped, CommandKind.DELETE_SPACE, {"slug": "personal"})
    assert r["status"] == "failed"
    assert r["error_code"] == "forbidden"


# ────────────────────────────────────────────────────────────────
# Area
# ────────────────────────────────────────────────────────────────

async def test_install_area_by_slug(bootstrapped):
    r = await _send(bootstrapped, CommandKind.INSTALL_AREA, {
        "space_slug": "personal", "area_slug": "meetings",
    })
    assert r["status"] == "applied", r
    st = (await bootstrapped["client"].get("/_status")).json()
    slugs = {a["slug"] for a in st["spaces"][0]["areas"]}
    assert "meetings" in slugs


async def test_install_area_conflict(bootstrapped):
    r = await _send(bootstrapped, CommandKind.INSTALL_AREA, {
        "space_slug": "personal", "area_slug": "personal_organization",
    })
    assert r["status"] == "failed"
    assert r["error_code"] == "conflict"


async def test_install_area_unknown_slug(bootstrapped):
    r = await _send(bootstrapped, CommandKind.INSTALL_AREA, {
        "space_slug": "personal", "area_slug": "made_up_area",
    })
    assert r["status"] == "failed"
    assert r["error_code"] == "unknown_area"


async def test_uninstall_and_reinstall_area(bootstrapped):
    r = await _send(bootstrapped, CommandKind.UNINSTALL_AREA, {
        "space_slug": "personal", "area_slug": "personal_organization",
    })
    assert r["status"] == "applied"
    # Now re-install should re-enable
    r = await _send(bootstrapped, CommandKind.INSTALL_AREA, {
        "space_slug": "personal", "area_slug": "personal_organization",
    })
    assert r["status"] == "applied"


# ────────────────────────────────────────────────────────────────
# Agents (metadata only in v0.7)
# ────────────────────────────────────────────────────────────────

async def test_deploy_agent_records_intent(bootstrapped):
    r = await _send(bootstrapped, CommandKind.DEPLOY_AGENT, {
        "space_slug": "personal",
        "area_slug": "personal_organization",
        "agent_slug": "task_router",
        "config": {"llm_role": "worker"},
    })
    assert r["status"] == "applied"


async def test_kill_switch_agent(bootstrapped):
    r = await _send(bootstrapped, CommandKind.KILL_SWITCH_AGENT, {
        "agent_slug": "runaway_agent", "reason": "cost spike",
    })
    assert r["status"] == "applied"


# ────────────────────────────────────────────────────────────────
# LLM
# ────────────────────────────────────────────────────────────────

async def test_set_llm_provider(bootstrapped):
    r = await _send(bootstrapped, CommandKind.SET_LLM_PROVIDER, {
        "role": "worker", "model": "claude-haiku-4",
    })
    assert r["status"] == "applied"


async def test_set_llm_provider_bad_role(bootstrapped):
    r = await _send(bootstrapped, CommandKind.SET_LLM_PROVIDER, {
        "role": "president", "model": "claude-opus-4",
    })
    assert r["status"] == "failed"
    assert r["error_code"] == "invalid_payload"


# ────────────────────────────────────────────────────────────────
# Platform lifecycle
# ────────────────────────────────────────────────────────────────

async def test_pause_and_resume(bootstrapped):
    r = await _send(bootstrapped, CommandKind.PAUSE, {})
    assert r["status"] == "applied"
    r = await _send(bootstrapped, CommandKind.PAUSE, {})
    assert r["status"] == "failed"
    r = await _send(bootstrapped, CommandKind.RESUME, {})
    assert r["status"] == "applied"


async def test_upgrade_platform_requires_version(bootstrapped):
    r = await _send(bootstrapped, CommandKind.UPGRADE_PLATFORM, {})
    assert r["status"] == "failed"
    r = await _send(bootstrapped, CommandKind.UPGRADE_PLATFORM, {
        "target_version": "0.8.0",
    })
    assert r["status"] == "applied"

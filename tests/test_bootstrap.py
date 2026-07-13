"""End-to-end tests for /_bootstrap and /_commands."""

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
        name="test-instance",
        persona=PersonaConfig(display_name="Rodrigo Test", kind="personal"),
        deployment=DeploymentConfig(modality="local", runtime="in_process"),
        llms=LlmConfig(
            enabled_providers=["ollama"],
            roles=LlmRoleAssignment(
                planner="llama3.1:70b", coordinator="llama3.1:8b",
                worker="llama3.1:8b", embeddings="nomic-embed-text",
            ),
        ),
        memory=MemoryConfig(driver="sqlite"),
        areas=AreasConfig(enabled=["personal_organization", "meetings"]),
        governance=GovernanceConfig(auth=AuthConfig(provider="password_totp")),
    )


async def test_health(client):
    r = await client.get("/_health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_bootstrap_flow(client):
    console_kp = ConsoleKeypair.generate()
    instance_id = uuid4()
    req = BootstrapRequest(
        instance_id=instance_id,
        console_public_key_pem=console_kp.public_pem(),
        console_webhook_url="http://console.test/callbacks",
        manifest=_manifest(),
    )

    r = await client.post(
        "/_bootstrap",
        json=req.model_dump(mode="json"),
        headers={"X-Bootstrap-Token": "test-token"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "platform_public_key_pem" in data
    assert set(data["applied_areas"]) == {"personal_organization", "meetings"}

    # Status endpoint reflects state
    r = await client.get("/_status")
    assert r.status_code == 200
    st = r.json()
    assert st["name"] == "test-instance"
    assert st["persona_kind"] == "personal"
    assert st["agent_runtime"] == "in_process"
    assert st["auth_provider"] == "password_totp"
    assert len(st["spaces"]) == 1
    assert st["spaces"][0]["slug"] == "personal"
    assert len(st["spaces"][0]["areas"]) == 2


async def test_bootstrap_rejects_bad_token(client):
    console_kp = ConsoleKeypair.generate()
    req = BootstrapRequest(
        instance_id=uuid4(),
        console_public_key_pem=console_kp.public_pem(),
        console_webhook_url="http://console.test/callbacks",
        manifest=_manifest(),
    )
    r = await client.post(
        "/_bootstrap",
        json=req.model_dump(mode="json"),
        headers={"X-Bootstrap-Token": "wrong"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "invalid_token"


async def test_command_verifies_signature(client):
    # bootstrap first
    console_kp = ConsoleKeypair.generate()
    instance_id = uuid4()
    req = BootstrapRequest(
        instance_id=instance_id,
        console_public_key_pem=console_kp.public_pem(),
        console_webhook_url="http://console.test/callbacks",
        manifest=_manifest(),
    )
    await client.post(
        "/_bootstrap",
        json=req.model_dump(mode="json"),
        headers={"X-Bootstrap-Token": "test-token"},
    )

    # signed command
    now = int(time.time())
    env = CommandEnvelope(
        instance_id=instance_id,
        issued_at=now,
        expires_at=now + 300,
        command=Command(kind=CommandKind.CREATE_SPACE, payload={"name": "acme"}),
    )
    token = sign_command(console_kp, env)
    r = await client.post("/_commands", content=token, headers={"Content-Type": "application/jwt"})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    # bad signature (different key)
    other = ConsoleKeypair.generate()
    bad_token = sign_command(other, env)
    r = await client.post("/_commands", content=bad_token, headers={"Content-Type": "application/jwt"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["error_code"] == "invalid_signature"


async def test_command_before_bootstrap_fails(client):
    r = await client.post("/_commands", content="fake", headers={"Content-Type": "application/jwt"})
    assert r.status_code == 412

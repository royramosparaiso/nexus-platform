# Nexus Platform

**The data plane of Nexus OS.** One instance per tenant — runs agents, LLM router, spaces, memory, and audit log for a single Persona.

> **Status:** ⚠️ Bootstrap phase. Repo initialized with architecture decision records (ADRs). Code coming after ADRs are approved.

## What lives here

- **Agent runtime** — decides how agents execute (see [ADR-001](docs/adr/001-agent-runtime.md)).
- **User authentication** — how humans log in to this instance (see [ADR-002](docs/adr/002-user-authentication.md)).
- **LLM router** — dispatches calls by role to providers (see [ADR-003](docs/adr/003-llm-router.md)).
- **Bootstrap endpoint** — accepts a signed `InstanceManifest` from Console, applies it.
- **Command endpoint** — executes signed commands from Console.
- **Personal Space + N project/group/company Spaces** — collaborative containers.
- **Areas** — installable modules (personal_organization, meetings, sales, etc.).

## What does NOT live here

- Wizards, deployers, credentials vault — that's [nexus-console](https://github.com/royramosparaiso/nexus-console).
- Shared types + JWT contracts — that's [nexus-core](https://github.com/royramosparaiso/nexus-core).

## Contracts

Platform depends on `nexus-core` for all wire types and JWT signing/verification. Version alignment: `nexus-core@0.6.x` = protocol v0.6.

## Design principles

1. **Zero-friction install.** `docker run` or `pip install` starts a working Platform. Everything else optional.
2. **Sovereign runtime.** No external dependency for critical paths (LLM router, auth for personal instances, secrets).
3. **Auditable.** Every LLM call, every action, every secret access is logged with signed provenance.
4. **Killable.** Global kill switch, per-agent kill switch, per-area budget ceiling.

## Development status

Nothing to run yet. ADRs are being reviewed. When approved, Platform code starts here.

## License

MIT — impulsado por Ironbat Digital LLC.

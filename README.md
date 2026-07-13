# nexus-platform

Data plane for Nexus OS. One Platform instance per tenant. Provisioned by
the Console via a signed bootstrap handshake, then driven by signed commands.

Protocol version: **v0.6** (contracts live in [`nexus-core`](https://github.com/royramosparaiso/nexus-core)).

## What's in v0.6

- FastAPI service exposing `/_health`, `/_bootstrap`, `/_commands`, `/_status`
- Postgres schema + Alembic migrations (5 tables: instance, space, area,
  user_account, audit_log)
- Bootstrap handshake with Ed25519 keypair generation
- Command verification via signed JWT (Console pubkey pinned at bootstrap)
- Personal Space created automatically on bootstrap with the areas from the
  Console manifest
- Runtime `in_process` (Redis workers reserved for v0.7 — see
  [ADR-001](docs/adr/001-agent-runtime.md))
- Auth providers: `password_totp | magic_link | oauth_google |
  oauth_microsoft | oauth_github | console_idp | clerk` (see
  [ADR-002](docs/adr/002-user-authentication.md))
- LLM adapters shipped: Anthropic, OpenAI, Ollama, OpenRouter (see
  [ADR-003](docs/adr/003-llm-router.md))

## What's NOT in v0.6 yet

- Full command dispatch (v0.6 accepts + queues; handlers land next PR)
- Live agent runtime (agents defined, scheduler stubbed)
- Notification webhooks back to Console (contract done, publisher TODO)
- Redis workers runtime backend (v0.7)

## Local development

```bash
# 1. Start Postgres
docker run --rm -d --name nexus-pg -p 5432:5432 \
  -e POSTGRES_USER=nexus -e POSTGRES_PASSWORD=nexus -e POSTGRES_DB=nexus postgres:16

# 2. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ../nexus-core/python
pip install -e ".[dev]"

# 3. Migrate + run
export PLATFORM_DATABASE_URL="postgresql+psycopg://nexus:nexus@localhost:5432/nexus"
export PLATFORM_BOOTSTRAP_TOKEN="dev-token"
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
pytest -q
```

Tests use SQLite in-memory — no Postgres required.

## Docker image

```bash
docker build -t nexus-platform:0.6.0 .
```

## License

MIT — Ironbat Digital LLC

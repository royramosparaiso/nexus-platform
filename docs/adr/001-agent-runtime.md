# ADR-001: Agent Runtime

**Status:** Approved 2026-07-13 with amendment: user-selectable per instance.
**Context:** How do agents execute inside a Nexus Platform instance? This choice affects installation friction, resource limits, blast radius of a runaway agent, and how we implement kill-switch.

## Options analyzed

### Option A — Async workers in-process

Every agent is an async coroutine running inside the same FastAPI process. FastAPI has a task pool (`asyncio.TaskGroup`), each agent gets a slot.

**How it works:**
- Platform starts → asyncio event loop starts.
- On `install_area` command, Platform creates N coroutines (one per agent), schedules them.
- Each agent has an inbox queue (`asyncio.Queue`) and a state machine.
- Kill-switch = cancel the coroutine + close its queue.

**Advantages:**
- Zero extra infra. `docker run nexus/platform` and everything works.
- Shared memory: agents can directly read from the same Postgres pool, LLM router, credentials vault — no serialization overhead.
- Fastest startup: agent up in <100ms.
- Simplest debugging: one process, one log stream, one debugger attaches.
- Lowest resource footprint: ~150 MB RAM for the whole Platform with 10 agents idle.

**Disadvantages:**
- No isolation: a bug in one agent (infinite loop, OOM, bad C extension) crashes the whole Platform.
- No CPU parallelism: async is I/O concurrency only. If an agent does heavy CPU work (embedding models locally, PDF processing), it blocks the event loop. Mitigation: send CPU-heavy tasks to `run_in_executor` with a bounded thread pool. But complex work still needs external service (Ollama, etc.).
- Kill-switch is cooperative: `task.cancel()` only works if the agent yields to the event loop. An agent stuck in a synchronous infinite loop can't be cancelled without killing the whole process.
- Rate limits shared: if agent A hammers the LLM, agent B's requests wait in the same connection pool.

**Best for:** 1-20 agents, single-tenant instance, personal/small-team use. This is the target profile of most Nexus users.

**Rough scale ceiling:** ~50 concurrent agents on a 2-vCPU 4GB machine before contention becomes painful.

---

### Option B — Redis queue + separate worker processes

Platform (FastAPI) enqueues agent tasks into Redis; N worker processes pull from the queue and execute.

**How it works:**
- Platform receives event (webhook, cron, user action) → publishes to Redis stream.
- Workers (`nexus-platform-worker` process, N replicas) consume from stream.
- Each worker holds M agent state machines. Multiple workers = horizontal scale.
- Kill-switch = mark agent as killed in DB, workers drop its messages.

**Advantages:**
- Horizontal scale: add worker replicas, throughput grows linearly.
- Real isolation: a worker crash only kills its N agents; Platform HTTP API stays up.
- Backpressure natural: Redis stream grows if agents are slow, gives observability.
- Rate limiting per-worker: each worker has its own LLM client pool.
- Battle-tested pattern (Celery/RQ/Sidekiq/Arq all use this).

**Disadvantages:**
- Extra dependency: Redis (or Valkey) must be installed and networked.
- Docker Compose grows from 2 to 3 services minimum (Postgres + Redis + Platform + Worker = 4).
- Serialization tax: every message JSON-encoded, agent state fetched from DB on every step.
- Cold start slower: worker startup + queue subscribe.
- More things that can fail: Redis restart, Redis OOM, network partition Redis↔worker.
- Installation friction: user needs to expose one more port, back up one more service.

**Best for:** Company/community instances with 50+ concurrent agents, multi-tenant on shared infra, or agents that do heavy background work independent of HTTP requests.

**Rough scale ceiling:** essentially unbounded — scale by adding workers.

---

### Option C — Docker sandbox per agent

Each agent runs in its own container spawned via Docker socket.

**How it works:**
- Platform receives `install_area` → calls Docker API to create container `nexus/agent-runtime:v0.6` with agent config as env vars.
- Agent container connects back to Platform over gRPC or HTTP with its instance credentials.
- Kill-switch = `docker kill`.

**Advantages:**
- True isolation: agent OOM only kills its container, cgroup limits enforceable per agent.
- Language-agnostic: an agent could be written in any language, packaged as an image.
- Resource limits per agent: CPU, RAM, network egress rules.
- Ecosystem: works with any container orchestrator (Docker Compose, K8s, Nomad).

**Disadvantages:**
- **Deal-breaker for installation friction.** Requires Docker socket access from Platform (or Docker-in-Docker). Fly.io doesn't support this cleanly. Cloud Run doesn't allow it. K8s requires Job/CronJob CRs with RBAC setup. Local Docker works but Docker Desktop on Mac/Windows is slow and painful.
- Cold start: 2-5s per agent boot vs 100ms in-process.
- Resource waste: 30-50 MB overhead per container × N agents.
- Networking: agents need to reach Platform → mesh, DNS, or shared network.
- Debugging: distributed system across containers, harder to trace.
- Security: giving Platform access to Docker socket = giving it root on the host.

**Best for:** Multi-tenant public SaaS where agents run untrusted user code (like a Firebase Functions for agents). This is not the Nexus target.

---

## Comparison table

| Dimension                 | A: in-process       | B: Redis + workers      | C: Docker sandboxes     |
|---------------------------|---------------------|-------------------------|-------------------------|
| Install friction          | **Zero**            | Medium (extra service)  | High (Docker access)    |
| Extra deps                | None                | Redis                   | Docker socket + images  |
| Startup per agent         | ~100ms              | ~500ms                  | 2-5s                    |
| Kill-switch effectiveness | Cooperative         | Reliable                | Absolute (`docker kill`)|
| Isolation                 | None                | Process-level           | Container-level         |
| CPU parallelism           | Limited (executor)  | Yes (per worker)        | Yes (per container)     |
| Scale ceiling             | ~50 agents / node   | Unbounded (add workers) | Bounded by host         |
| Ops complexity            | Very low            | Medium                  | High                    |
| Language flexibility      | Python only         | Python only             | Any                     |
| Cost at small scale       | Minimal             | +Redis                  | +overhead per container |
| Blast radius on crash     | Whole Platform      | One worker              | One agent               |

---

## Recommendation (amended)

**Support both A and B as a per-instance choice in the wizard.** Deployment step 2 gains a `runtime` field:

- `in_process` (default) — Option A. Zero infra beyond Postgres.
- `redis_workers` — Option B. Adds a Redis service to the compose/deploy manifest.

Option C (Docker sandboxes) remains excluded for v0.6 — comes back post-v1.0 for Client Space scenarios with untrusted agents.

### Defaults per Persona kind

| Persona kind | Default runtime | Rationale                                              |
|--------------|------------------|--------------------------------------------------------|
| personal     | in_process       | 1-5 agents, personal machine, offline-friendly         |
| family       | in_process       | Same as personal                                       |
| company      | in_process       | Start simple; upgrade via wizard when concurrency grows|
| community    | in_process       | Same                                                   |
| client       | redis_workers    | Multi-tenant, isolation matters                        |
| custom       | (user choice)    | No default                                             |

### Installation friction cost of adding Redis

- **Docker Compose local:** +1 service (6 lines in compose). Zero user-visible friction — the installer's compose file already includes it when the wizard selects `redis_workers`.
- **Fly.io:** +1 `flyctl apps create` command run by the deployer automatically. 2 minutes extra.
- **Native (no Docker) dev:** user must install Redis (brew/apt). This is the one setup where friction is real. Documented in the wizard warning.
- **Backup:** +1 dump target (Redis RDB snapshot).
- **RAM:** +30-50 MB idle.

Net: with Docker Compose or Fly (the modalities most users pick), the friction is essentially absorbed by the deployer. Bare-metal is the only case with real added complexity.

### v0.6 implementation scope

- Wizard schema accepts the `runtime` field.
- Only `in_process` is functional in v0.6.
- `redis_workers` returns a wizard warning: "Redis runtime available in v0.7 — using in-process for now."
- The scheduler interface is defined once; v0.7 swaps the backend without touching agent code.

### Migration path A → B (v0.7+)

Same agent state machine interface. Change happens in wizard step 2 → Console emits `set_runtime` command → Platform boots workers → drains in-process scheduler → cuts over. No agent code touched, no data migration.

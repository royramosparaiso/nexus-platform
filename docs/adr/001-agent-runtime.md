# ADR-001: Agent Runtime

**Status:** Draft — pending decision.
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

## Recommendation

**Start with Option A (in-process async).** Reasoning:

1. Zero-friction installation is a hard requirement — Option A is the only one that keeps `docker run` to a single container beyond the DB.
2. The 90th-percentile Nexus user has < 10 agents. Option A handles this comfortably.
3. Migrating A → B is straightforward when needed: the agent state machine has the same interface either way; we swap the scheduler.
4. Option C is a non-starter for the current target (personal/company instances). It might return as an opt-in later for sandboxing untrusted code in the Client Space scenario.

## Migration path

- v0.6 → v0.9: in-process. Add executor pool for CPU tasks (embeddings, PDF).
- v0.10+: introduce Redis as *optional*. Same agent code, different scheduler. Compose users get Redis included automatically; local users can opt out and stay in-process.
- Post-v1.0: Option C returns as an opt-in for Client Space (untrusted agents from third parties).

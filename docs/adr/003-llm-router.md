# ADR-003: LLM Router

**Status:** Approved 2026-07-13 with amendment: OpenRouter included in v0.6.
**Context:** Nexus supports 8 LLM providers today (Anthropic, OpenAI, OpenRouter, Perplexity, Groq, Together, Mistral, Ollama) and routes calls by role (planner/coordinator/worker/embeddings). Do we build our own router or depend on LiteLLM?

## Options analyzed

### Option A — LiteLLM as a dependency

LiteLLM is an OSS Python library (BerriAI/litellm) that provides a unified API across 100+ LLM providers. Format is OpenAI-compatible.

**How it works:**
```python
from litellm import completion
r = completion(model="anthropic/claude-3-5-sonnet", messages=[...])
```

We wrap it with a thin `LlmRouter` class that maps `role → model_string`.

**Advantages:**
- Battle-tested: used by many prod systems (Palantir, Discord bots, etc.).
- 100+ providers supported out of the box, kept up to date.
- Handles streaming, tool use, vision, function calling uniformly.
- Cost tracking, retries, fallback built-in.
- Active community, weekly releases.
- Saves 3-6 weeks of engineering time.

**Disadvantages:**
- **Big dependency.** ~200 transitive packages. Adds ~80 MB to the container. Increases attack surface.
- **Not stable.** Breaks between minor versions. Some providers work partially. Bug reports pile up faster than fixes.
- **Opaque telemetry.** LiteLLM phones home unless explicitly disabled.
- **License drift.** OSS core + hosted proxy commercial product. Occasional feature gating.
- **Version pinning becomes a chore.** Weekly bumps mean you either freeze (miss provider updates) or track upstream (break things).

---

### Option B — Own minimal router with per-provider adapters

We write our own router with one adapter per provider we actually support.

**How it works:**
- `LlmRouter` has a table `{provider: AdapterClass}`.
- Each adapter is ~100 lines: format messages, POST to provider, parse response, handle streaming.
- Roles → provider+model configured in the manifest.
- Fallback, retries, budget tracking are our code — auditable, testable.

**Advantages:**
- **No hidden magic.** You can read every line and know what happens on an LLM call.
- **Small.** 8 adapters × ~120 LOC = ~1000 LOC total, dependency-free (only `httpx` + `pydantic`, already used elsewhere).
- **Zero telemetry.** Nothing phones home unless we write it.
- **Perfect fit for budget tracking + kill-switch.** Every call passes through our code; audit is trivial.
- **Long-term stability.** We control the surface. New provider = one file added, no vendor to wait on.
- **Observability wins.** Every call has our own request ID, structured logs, span attributes.
- **Sovereignty aligned.** No external dependency for the most critical runtime function.

**Disadvantages:**
- We write and maintain the code. 200-300 hours over the first year for the 8 providers + streaming + tool use.
- Missing edge cases (image inputs, prompt caching, structured outputs) require extra work per provider.
- Fewer providers than LiteLLM. But 8 is all Nexus advertises, so this is aligned.

---

### Option C — Hybrid: LiteLLM for basic calls, own wrapper for orchestration

Use LiteLLM under the hood, wrap with our own layer that adds role routing, budget tracking, audit log.

**Advantages:**
- Faster to ship: don't write adapters for 8 providers.
- Own layer holds business rules; LiteLLM handles wire format.

**Disadvantages:**
- Still carries LiteLLM's dependency footprint and instability.
- Two layers to debug: is the bug ours or theirs?
- Doesn't reduce sovereignty risk.

---

## Comparison table

| Dimension                    | A: LiteLLM        | B: Own router      | C: Hybrid          |
|------------------------------|-------------------|--------------------|--------------------|
| Time to first call working   | 1 day             | 1 week             | 2 days             |
| External dependency          | Heavy             | **None**           | Heavy              |
| Providers supported          | 100+              | 8 (our target)     | 100+ via LiteLLM   |
| Container size impact        | +80 MB            | +0 MB              | +80 MB             |
| Bug ownership                | LiteLLM + us      | Us                 | Us + LiteLLM       |
| Audit trail quality          | Depends on their  | Fully controllable | Ours atop theirs   |
| Telemetry                    | Phones home       | None               | Phones home        |
| Sovereign narrative          | Weak              | **Strong**         | Weak               |
| Update cadence               | Weekly (risk)     | Ours to set        | Weekly (risk)      |
| Suitability for kill-switch  | Cooperative       | **Exact control**  | Cooperative        |

## Recommendation

**Option B — own minimal router.** Rationale aligned with your request "the option that makes the system least dependent":

1. **Sovereignty.** LLM routing is the most critical runtime path in Nexus. Depending on a third party for it contradicts the "personal OS" narrative.
2. **Small surface, small maintenance.** 8 providers × ~120 LOC is < 1500 LOC. Any adapter is one file, one week of one dev-day to add or replace.
3. **Better observability.** Every call passes through our code — perfect audit log, perfect budget tracking, perfect kill-switch integration.
4. **No hidden behavior.** Explicit is better than magic.
5. **Migration cost is real.** LiteLLM as a starting point creates lock-in: months of code assumes LiteLLM behavior; extracting later is expensive.

## Implementation plan (v0.6, amended)

Ship **4 adapters in v0.6**: Anthropic, OpenAI, Ollama, OpenRouter. OpenRouter alone re-exposes 200+ models under a unified OpenAI-compatible interface, so users can reach almost any model in the ecosystem without waiting for us to add a native adapter.

Structure:

```
platform/app/llm/
├── router.py           # LlmRouter with role→model dispatch
├── budget.py           # Token accounting + monthly budget alerts
├── audit.py            # Every call logged to nexus_audit_log table
├── adapters/
│   ├── base.py         # LlmAdapter ABC: complete, stream, embed
│   ├── anthropic.py    # native SDK
│   ├── openai.py       # native SDK
│   ├── ollama.py       # local, httpx
│   └── openrouter.py   # httpx (OpenAI-compatible protocol)
└── errors.py
```

Each adapter is < 200 LOC. OpenRouter reuses the OpenAI adapter's message-formatting code because both speak the same protocol.

## Migration path

- v0.6: 4 adapters (Anthropic, OpenAI, Ollama, OpenRouter).
- v0.7: Groq, Together, Mistral, Perplexity as native adapters (they all offer OpenAI-compatible modes, so the code is thin).
- Long term: if we want to add exotic providers not covered by OpenRouter, vendor their protocol into a new adapter file. No framework dependency ever needed.

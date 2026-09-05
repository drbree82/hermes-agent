# Optional ARC-inspired reasoning substrate

Status: experimental implementation on `arc-continuous-hermes`

## Intent

Hermes remains the agent product: its CLI/TUI, session database, tools,
skills, memory, MCP, browser/computer integrations, approvals, delegation,
messaging, scheduling, streaming, and steering remain the owner of the user
experience. This work adds a selectable reasoning-loop seam underneath that
product.

The initial configuration is:

```yaml
reasoning_backend: legacy
# or
reasoning_backend: arc_continuous
```

`legacy` calls the current Hermes conversation loop. `arc_continuous` uses the
same loop and tool dispatch, but adds a durable working-state lifecycle and a
request-local context projection. This deliberately keeps the Hermes product
and its tool ecosystem in one loop while changing how cognition is carried
between model/tool interactions.

## What was learned from ARC

The transferable ARC-AGI-3 ideas are architectural rather than game-specific:

* a stable provider/runtime adapter interface;
* a versioned, JSON-serializable runtime-state envelope;
* separate state strategies for provider-native continuation and generic
  rolling history;
* preservation of provider output items that carry hidden/reasoning state;
* compaction as a state transition that prunes history after the newest
  compaction item;
* timestamped trajectory/telemetry events that can be compared across runs.

The current ARC implementation makes continuity concrete in two places:
`BenchmarkingAgent` owns a conversation/runtime state across iterations, and
the provider adapter receives only the new turn plus the provider-owned state
handle when the selected runtime supports it. Its OpenAI Responses path either
chains `previous_response_id` or replays native output items (including
encrypted reasoning and compaction items) when operating statelessly. The
generic Hermes adaptation is therefore a working-set projection, not a copied
game loop.

These ideas are adapted to Hermes' existing transcript, checkpoint, session,
and compression systems. Hermes does not import the ARC repositories at
runtime.

## Explicit non-goals

The ARC Arcade/GameClient/GameStep/GameAction environment protocol, game
frames, scorecards, ARC API authentication, task/game IDs, benchmark reward
logic, benchmark-specific agent prompts, and `ARC_API_KEY` are not part of
normal Hermes operation. ARC game functionality is neither required nor
introduced by this change.

## Runtime shape

`agent/reasoning_backend.py` owns the backend registry, capability resolver,
runtime hooks, state metadata, and per-turn instrumentation. The existing
`AIAgent.run_conversation` wrapper selects a backend and delegates to the
existing `agent.conversation_loop.run_conversation` function. The loop keeps
its current tool, approval, retry, compression, interrupt, streaming, and
session behavior; backend hooks observe model calls, responses, tools,
compaction, and errors without moving those responsibilities out of Hermes.

Provider capability detection is centralized. Capabilities describe what the
resolved provider/transport actually supports, including native conversation
continuation, persistent reasoning state, provider-side compaction, local
compaction, reasoning effort, steering, tool continuation, and resumable
handles. A capability is never inferred merely because the selected backend
is named `arc_continuous`.

`agent/continuous_state.py` owns `ContinuousState`, a versioned and
JSON-serialisable envelope containing the objective, plan, working state,
facts, decisions, hypotheses, unresolved questions, tool observations,
artifacts, failures, constraints, completion criteria, compaction records and
checkpoint records. It is written atomically beside the normal Hermes logs or
under `~/.hermes/sessions/continuous_state/`. The state file is inspectable and
resumable; a corrupt auxiliary file is ignored so the canonical SQLite
transcript still loads.

For Tier 2 providers (local Qwen/llama.cpp/vLLM/SGLang, Inkling, ordinary
OpenAI-compatible relays), each request keeps the active user turn and its
tool interactions, replaces older transcript turns in the API copy with the
working-state capsule, and leaves the persisted Hermes transcript unchanged.
The state is updated before every inference request and checkpointed at turn
start, each projected/native turn, trajectory compaction, and turn completion.
Trajectory compaction deduplicates redundant tool observations while retaining
failures, facts, decisions, artifacts and the full canonical transcript.

For Tier 3 Responses transports, the generic projection is intentionally
disabled. Hermes' existing Codex/Responses converter retains native encrypted
reasoning items, assistant `phase` fields, and native `compaction` items in the
canonical session/checkpoint path. This prevents the generic capsule from
destroying the provider's stronger state. Telemetry reports whether a turn
used `substrate_managed` or `provider_native` continuity.

## OpenAI Responses and Astra direction

The adapter targets the actual Responses interfaces: output items are replayed
as input items; reasoning encrypted content is retained when the endpoint
supports it; assistant `phase` values are preserved; and `/responses/compact`
plus `context_management` are capability gated rather than assumed. GPT-6
Astra is recognized as a current/future Responses model family for capability
resolution, while route/model gates remain conservative when access is
unavailable.

The existing Codex/Responses replay path remains the default Tier 3-compatible
path for relays and stateless deployments. Direct OpenAI Responses routes can
opt into the separate experimental `openai_native_continuous` backend, which
uses `store=true`, `previous_response_id`, and new tool-result input while
retaining Hermes' canonical transcript for rollback and fallback. The exact
capability boundary and current Astra semantics are documented in
[`docs/astra-native-continuation.md`](astra-native-continuation.md).

This is an exact compatibility boundary, not a claim that encrypted replay is
identical to server-side response continuation. Mid-turn WebSocket steering is
likewise represented as a capability only when an implementation exists;
Hermes' existing safe-boundary steering remains unchanged.

## Instrumentation and evaluation

Each backend emits a structured per-turn report containing success/failure,
model-call attempts, input/output/cached/reasoning/total tokens, context
samples, tool calls and repeated-tool diagnostics, duration, estimated cost
when available, local/trajectory/provider compaction events, retries/errors,
checkpoint events, and native-state reuse. Reports are attached to the normal
result and persisted as sanitized runtime metadata; opaque provider reasoning
blobs are not copied into telemetry. Normal sessions append records to
`~/.hermes/sessions/reasoning_metrics.jsonl`; a one-shot `--usage-file` also
embeds the per-run record for scripts.

`scripts/compare_reasoning_backends.py` runs the same one-shot prompt once
with each backend and writes a machine-readable comparison. It is an
evaluation aid, not a second agent product; it invokes the normal Hermes CLI.
For a repeatable fixture task:

```bash
.venv/bin/python scripts/compare_reasoning_backends.py \
  --task-file benchmarks/reasoning_tasks.json \
  --task-id coding_broken_repo \
  --model thinkingmachines/inkling:free --provider openrouter \
  --output /tmp/coding-ab.json
```

The fixture is copied separately for each backend. The full catalog can be
run with `scripts/run_reasoning_benchmarks.py`. These tasks are deliberately
Hermes tasks, not ARC games; live runs require the user's normal provider
credentials and should report provider failures/timeouts separately from task
success.

## Incremental follow-up work

1. Add the backend/capability seam and telemetry while preserving the current
   loop. (complete)
2. Add the generic state lifecycle, trajectory projection, compaction and
   checkpoints. (complete in this phase)
3. Validate the two backends on identical real Hermes tasks.
4. Extend provider adapters only where a provider's documented native state is
   available, with capability tests and explicit fallbacks.
5. Consider optional direct-OpenAI response-id handles after measuring real
   task outcomes and resolving the `store=true` privacy/retry contract.

The success criterion is improved or equal real-world Hermes task performance,
not resemblance to an ARC benchmark harness.

## Sources inspected

* ARC Prize benchmarking runtime: <https://github.com/arcprize/arc-agi-3-benchmarking>
* ARC Prize agent examples: <https://github.com/arcprize/ARC-AGI-3-Agents>
* ARC Prize game toolkit (benchmark-specific and intentionally excluded): <https://github.com/arcprize/ARC-AGI>
* OpenAI latest-model guidance: <https://developers.openai.com/api/docs/guides/latest-model>
* OpenAI Responses compaction reference: <https://developers.openai.com/api/reference/java/resources/responses/methods/compact>

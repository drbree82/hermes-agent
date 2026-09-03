# Optional ARC-inspired reasoning substrate

Status: experimental design and first implementation

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
same loop and tool dispatch for the first slice, but gives it a runtime
adapter boundary, explicit capability reporting, durable telemetry, and a
provider-neutral state/checkpoint contract. This deliberately makes the
smallest change that can run an unchanged Hermes task through either backend.

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

For Responses transports that preserve native output items, the existing
Hermes Codex/Responses replay path remains authoritative. The continuous
backend records the native-state reuse and keeps the opaque provider items in
the existing session/checkpoint transcript. For local or Chat Completions
providers, the backend reports native-state capabilities as unavailable and
uses Hermes' explicit transcript, tool-result persistence, checkpoints, and
local compaction as the generic approximation.

## OpenAI Responses and Astra direction

The adapter targets the actual Responses interfaces: output items are replayed
as input items; reasoning encrypted content is retained when the endpoint
supports it; and `/responses/compact` plus `context_management` are capability
gated rather than assumed. GPT-6 Astra is recognized as a future/current
Responses model family for capability resolution, while route/model gates
remain conservative when access is unavailable.

The first slice does not invent a server-side `previous_response_id` policy
for Hermes. Hermes needs its own durable transcript, tool approvals, steering,
and cross-provider session semantics. A future provider adapter can opt into
server-side response handles once those semantics are explicitly reconciled.
Mid-turn WebSocket steering is likewise represented as a capability only when
an implementation exists; Hermes' existing safe-boundary steering remains
unchanged.

## Instrumentation and evaluation

Each backend emits a structured per-turn report containing success/failure,
model-call attempts, input/output/reasoning tokens, context samples, tool
calls, duration, estimated cost when available, compaction events,
retries/errors, and native-state reuse. Reports are attached to the normal
result and persisted as sanitized runtime metadata; opaque provider reasoning
blobs are not copied into telemetry. Normal sessions append records to
`~/.hermes/sessions/reasoning_metrics.jsonl`; a one-shot `--usage-file` also
embeds the per-run record for scripts.

`scripts/compare_reasoning_backends.py` runs the same one-shot prompt once
with each backend and writes a machine-readable comparison. It is an
evaluation aid, not a second agent product; it invokes the normal Hermes CLI.

## Incremental follow-up work

1. Add the backend/capability seam and telemetry while preserving the current
   loop.
2. Validate the two backends on identical real Hermes tasks.
3. Extend provider adapters only where a provider's documented native state is
   available, with capability tests and explicit fallbacks.
4. Consider optional provider server-state handles and richer trajectory
   replay after measuring real task outcomes.

The success criterion is improved or equal real-world Hermes task performance,
not resemblance to an ARC benchmark harness.

## Sources inspected

* ARC Prize benchmarking runtime: <https://github.com/arcprize/arc-agi-3-benchmarking>
* ARC Prize agent examples: <https://github.com/arcprize/ARC-AGI-3-Agents>
* ARC Prize game toolkit (benchmark-specific and intentionally excluded): <https://github.com/arcprize/ARC-AGI>
* OpenAI latest-model guidance: <https://developers.openai.com/api/docs/guides/latest-model>
* OpenAI Responses compaction reference: <https://developers.openai.com/api/reference/java/resources/responses/methods/compact>

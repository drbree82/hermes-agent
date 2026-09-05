# GPT-6 Astra native continuation

This document records the Astra integration boundary as of 2026-09-05. It is
separate from the generic `arc_continuous` state projection: the latter is a
Hermes-managed Tier-2 fallback for providers that do not expose an opaque
state handle.

## Current OpenAI semantics

The current OpenAI documentation describes GPT-6 Astra as a Responses API
model. The relevant mechanisms are:

| Mechanism | Current support | Hermes treatment |
| --- | --- | --- |
| Persisted reasoning | Responses reasoning items can be retained and, where required, replayed with encrypted content. | Existing Codex/Responses replay remains available. |
| `previous_response_id` | A stored response can be the parent of the next response. It cannot be combined with `conversation`. | Implemented only for the direct OpenAI API capability. |
| Conversation state | The Responses conversation facility is a separate server-side state mechanism. | Not mixed with `previous_response_id`; no Hermes conversation id is invented. |
| Provider compaction | Responses supports automatic compaction through `context_management` and a standalone compaction endpoint. | Existing native compaction remains capability-gated. |
| Encrypted reasoning | `reasoning.encrypted_content` may be included and replayed for state continuity, including stateless/ZDR-compatible flows. | Preserved in the canonical Hermes transcript/checkpoint path. |
| Prompt caching | Stable prefixes and cache keys are supported; changing request prefixes reduces reuse. | Instructions/tools remain stable and native continuation sends only the new suffix. |
| Tool continuation | Function-call output is supplied as the next input item. | Native follow-ups send the new tool-result delta and the response handle. |
| Reasoning effort | Astra supports the documented reasoning effort controls. | Hermes passes the configured effort through capability-aware transport code. |
| Configuration updates | Astra supports WebSocket `configuration_update` for reasoning-effort changes while preserving cache state. | Not used by the HTTP streaming loop; Hermes keeps its existing turn-boundary controls. |
| Mid-turn steering | Astra supports WebSocket steering with `response.steer`; it does not undo already-started tools. | Not claimed by the HTTP adapter; existing Hermes safe-boundary steering remains active. |

Primary references: [Astra model guide](https://developers.openai.com/api/docs/guides/latest-model),
[Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
[reasoning](https://developers.openai.com/api/docs/guides/reasoning),
[compaction](https://developers.openai.com/api/docs/guides/compaction),
[conversation state](https://developers.openai.com/api/docs/guides/conversation-state),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), and
[mid-turn steering](https://developers.openai.com/api/docs/guides/steering).

## What Hermes did before native mode

Hermes' existing `codex_responses` path creates a new Responses request for
each tool turn. It sends `store:false`, reconstructs the input from the
canonical Hermes transcript, and preserves encrypted reasoning, assistant
message items, tool calls, and compaction checkpoints for replay. It did not
send `previous_response_id` and did not rely on OpenAI server-side response
storage.

That replay path is useful and remains the fallback. It is not equivalent to
server-side continuation: the provider must validate and process the replayed
items again, while a response handle lets OpenAI retain and resolve the
provider-owned trajectory.

## Native mode

`openai_native_continuous` is a separate experimental backend. On the direct
OpenAI Responses route it:

1. sends the first request with `store:true`;
2. records the opaque response id without recording reasoning text in
   telemetry;
3. sends subsequent user/tool-result input with `previous_response_id` and
   does not replay the same encrypted reasoning items in that request;
4. keeps the complete Hermes transcript and encrypted items for sessions,
   approvals, provider switching, recovery, and fallback;
5. clears the handle and permits transcript replay when the provider rejects
   or cannot resolve it.

The capability resolver, rather than the agent loop, decides whether the
active route supports this mode. OpenAI-compatible relays and the ChatGPT
Codex relay retain the existing encrypted-replay path unless their adapter
explicitly advertises the same server-state contract. Selecting the backend
must never falsely report native continuation.

Use it explicitly in configuration or a one-shot benchmark:

```yaml
reasoning_backend: openai_native_continuous
```

The direct API route also requires credentials and retention policy compatible
with `store:true`. If a deployment requires stateless/ZDR operation, use
`legacy` or `arc_continuous` with encrypted reasoning replay/native compaction
as available.

## ARC Prize concepts that were and were not ported

The ARC Prize adapter's reusable ideas are a persistent runtime state, a
provider adapter boundary, retaining native Responses output items, and
compaction as a state transition. Its server-state adapter similarly sends
new messages with a `previous_response_id`, while its continuous-conversation
adapter maintains serialized native input items for stateless operation.

Hermes ports those runtime semantics underneath its existing tools and
conversation loop. It does not port `GameClient`, `GameStep`, game actions,
scoring, benchmark prompts, game IDs, or `ARC_API_KEY`.

Reference: [ARC Prize ARC-AGI-3 benchmarking](https://github.com/arcprize/arc-agi-3-benchmarking).

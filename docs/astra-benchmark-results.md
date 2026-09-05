# GPT-6 Astra Hermes smoke results

Date: 2026-09-05  
Model: `gpt-6-astra`  
Reasoning effort: `high` from the active Hermes configuration  
Provider: `openai-codex` at `https://chatgpt.com/backend-api/codex`  
Validation: fixture validators, including pytest and the distributed-evidence
ledger validator

These are deliberately small smoke runs, not a production conclusion. The
configured credential is the ChatGPT Codex relay. The relay exposed Hermes'
encrypted reasoning/native-compaction path, but the capability resolver did
not advertise the direct OpenAI `previous_response_id` contract. Therefore
the `openai_native_continuous` smoke was a correctly labelled
`provider_native_replay_fallback`, not a true server-state experiment.

## Existing legacy versus ARC path

Each row is one isolated fixture copy and one run. Token counts are provider
usage fields; relay pricing is reported as included, so no dollar estimate is
available from these runs.

| Task | Backend | Validated | Model calls | Input tokens | Cached input | Output tokens | Tool calls | Wall time | Compactions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `coding_broken_repo` | `legacy` | yes | 16 | 31,704 | 184,448 | 747 | 10 | 62.8s | 0 |
| `coding_broken_repo` | `arc_continuous` | yes | 16 | 31,791 | 184,960 | 827 | 10 | 61.5s | 0 |
| `failure_recovery` | `legacy` | yes | 16 | 27,601 | 172,672 | 1,779 | 9 | 79.9s | 0 |
| `failure_recovery` | `arc_continuous` | yes | 16 | 27,910 | 172,928 | 1,935 | 9 | 90.3s | 0 |
| `long_context_distributed_evidence` | `legacy` | yes | 28 | 60,461 | 363,264 | 2,229 | 13 | 127.9s | 0 |
| `long_context_distributed_evidence` | `arc_continuous` | yes | 36 | 51,529 | 492,672 | 2,443 | 17 | 147.5s | 2 |

The coding row for `legacy` and the ARC row came from the initial capped
baseline; the later relay runs reproduced the same mechanically validated
coding task with small stochastic variation. The failure-recovery and
long-context rows were run together from the same benchmark invocation.

## Native-backend smoke

The separate native backend was also run on `coding_broken_repo`:

| Backend | Validated | Continuity reported | Native continuation requests | Native response ids | Input tokens | Model calls |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| `openai_native_continuous` on Codex relay | yes | `provider_native_replay_fallback` | 0 | 9 captured | 34,104 | 18 |

This proves the capability gating and telemetry path. It does not measure
`previous_response_id`; that requires a direct OpenAI API route with a
credential and retention policy that permit `store:true`.

## Interpretation

The current evidence says:

* Astra completed all smoke fixtures correctly under both existing modes.
* Short tasks showed no meaningful success benefit from the ARC substrate and
  slightly higher output/input usage in these samples.
* The long-context task showed lower uncached provider input (`60,461` to
  `51,529`) under ARC, but it also produced more model/tool calls and higher
  latency. The task still passed, so this is a context-cost tradeoff rather
  than a demonstrated quality improvement.
* The relay's native encrypted reasoning state is observable as response-item
  reuse, but that must not be reported as server-side response continuation.
* No conclusion about direct Astra `previous_response_id` performance should
  be drawn until a direct API run is available.

The production policy remains conservative: `legacy` is the default,
`arc_continuous` is adaptive/experimental, and
`openai_native_continuous` is opt-in and capability-gated.

## Reproduction

The paid runs used explicit caps:

```bash
source .venv/bin/activate
python scripts/run_reasoning_benchmarks.py \
  --task-file benchmarks/reasoning_tasks.json \
  --task-id failure_recovery \
  --task-id long_context_distributed_evidence \
  --model gpt-6-astra --provider openai-codex \
  --repetitions 1 --max-turns 10 --run-budget 150 --timeout 210 \
  --output-dir /tmp/astra-relay-ab
```

The direct API native path can be exercised once the active Hermes profile is
configured with the direct OpenAI provider/API credential:

```bash
python scripts/compare_reasoning_backends.py \
  --task-file benchmarks/reasoning_tasks.json \
  --task-id coding_broken_repo \
  --model gpt-6-astra --provider openai \
  --backends legacy arc_continuous openai_native_continuous \
  --max-turns 12 --run-budget 180 --timeout 240 \
  --output /tmp/astra-direct-abc.json
```

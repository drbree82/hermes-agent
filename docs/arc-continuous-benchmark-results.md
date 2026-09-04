# ARC-continuous Hermes benchmark results

Date: 2026-09-04  
Branch: `arc-continuous-hermes`  
Provider: OpenRouter  
Model: `thinkingmachines/inkling:free`  
Repetitions: 5 per task/backend, 25 paired comparisons (50 Hermes runs)

## Result

The ARC-inspired substrate did not improve this sample overall. All four
mechanically validated fixture tasks succeeded in 20/20 runs for both
backends. On the research task, the substrate-managed backend had fewer calls
and lower token usage, but its citation/completion heuristic passed only 2/5
runs versus 4/5 for legacy. It therefore did not demonstrate a real-world
task-success improvement in this experiment.

The generic Inkling route had no provider-native continuation capability. Every
`arc_continuous` run used Tier 2 substrate-managed continuity; no native
reasoning state was claimed or reused.

## Per-task medians

`Tokens` is the provider usage total recorded by the one-shot usage report.
`Time` is wall-clock seconds. Fixture success is determined by the mechanical
validator, not by the final answer. Research success is explicitly a weaker
heuristic: the run completed and its output contained source URLs.

| Task | Backend | Success rate | Median tokens | Median calls | Median time | Median tools | Cached input | Capsule tokens | Compactions |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| coding_broken_repo | legacy | 5/5 | 92,948 | 9 | 15.3s | 9 | 79,872 | 0 | 0 |
| coding_broken_repo | arc_continuous | 5/5 | 130,927 | 10 | 19.9s | 9 | 84,480 | 4,638 | 0 |
| sysadmin_broken_docker | legacy | 5/5 | 72,197 | 7 | 16.2s | 7 | 59,392 | 0 | 0 |
| sysadmin_broken_docker | arc_continuous | 5/5 | 100,110 | 8 | 16.0s | 7 | 65,664 | 2,786 | 0 |
| research_multi_step | legacy | 4/5* | 221,321 | 6 | 18.4s | 5 | 180,864 | 0 | 0 |
| research_multi_step | arc_continuous | 2/5* | 172,586 | 4 | 16.6s | 4 | 95,872 | 3,234 | 0 |
| long_horizon_inventory | legacy | 5/5 | 73,311 | 7 | 13.9s | 7 | 60,288 | 0 | 0 |
| long_horizon_inventory | arc_continuous | 5/5 | 96,049 | 8 | 15.9s | 7 | 65,536 | 2,862 | 0 |
| failure_recovery | legacy | 5/5 | 91,025 | 9 | 15.0s | 8 | 77,440 | 0 | 0 |
| failure_recovery | arc_continuous | 5/5 | 93,445 | 8 | 15.0s | 7 | 65,280 | 2,309 | 0 |

`*` Research has no artifact validator in this fixture. Its heuristic result
should not be compared with the mechanical fixture results as if it were an
equivalent objective test.

Provider-reported cost was unavailable/zero for the free Inkling route, so no
credible dollar comparison is reported. The raw per-repetition JSON traces
were written locally under `reasoning_benchmark_results/inkling-20260904/`.

## What changed in the traces

Legacy sent the ordinary Hermes transcript through the existing loop. The
ARC-inspired runs retained the same tool and approval path, but projected the
active turn plus a versioned working-state capsule. The capsule was populated
with bounded tool observations and verified operational facts; representative
coding runs had 4,638 median capsule tokens and 26,607 median serialized state
bytes. Checkpoint events were present on every substrate-managed run, and the
state reset/epoch counter recorded the initial task epoch.

The long-horizon fixture did not cross the current compaction threshold, so
this suite measured trajectory projection and checkpointing but did not
measure a genuine compaction benefit. No redundant tool calls were observed
in the medians. The one clear failure-recovery signal was fewer median model
and tool calls for `arc_continuous` (8/7 versus 9/8), but its total tokens were
slightly higher.

Representative paired traces:

* Coding repetition 1: both backends identified the whitespace-filter bug in
  `app.py` and passed the two pytest checks. Legacy used 8 model calls and the
  substrate used 9; the latter carried a 5,276-token capsule.
* Failure-recovery repetition 1: both validators confirmed `RECOVERY.md`.
  The ARC trace explicitly retained the missing `missing-runbook.md` failure
  and reported 7 calls/7 tools; legacy used 6 calls/6 tools in that specific
  repetition. This illustrates why one trace is not evidence of a general
  efficiency win.

## Interpretation

* Task success: no improvement on coding, sysadmin, inventory, or recovery;
  lower research heuristic success for `arc_continuous`.
* Efficiency: the capsule adds substantial uncached input context on short
  tasks. It reduced calls on recovery and research medians but did not reduce
  total provider token usage consistently.
* Latency: roughly unchanged for sysadmin/recovery, higher for coding and
  inventory, and lower for research. The differences are stochastic and the
  sample is small.
* State quality: the state machinery is observable and resumable, but these
  tasks were too short to prove that trajectory compaction improves work. A
  longer multi-turn evaluation is required before tuning thresholds.
* Transfer claim: this experiment provides no evidence that ARC-style runtime
  machinery automatically improves general Hermes work. The correct current
  default remains `legacy`; `arc_continuous` is an opt-in experiment.

## Local/OpenAI-compatible model check

The configured local Qwen-compatible route was checked at
`192.168.68.65:8086`; it was not reachable during this run. No local-model
results are claimed. This is an infrastructure availability result, not a
model-quality result. The benchmark runner remains provider/model agnostic and
can be rerun with a live llama.cpp/vLLM/SGLang endpoint.

## Reproduction

```bash
.venv/bin/python scripts/run_reasoning_benchmarks.py \
  --model thinkingmachines/inkling:free \
  --provider openrouter \
  --repetitions 5 \
  --timeout 180 \
  --output-dir reasoning_benchmark_results/inkling-20260904
```

The runner copies each fixture and an isolated Hermes home for each backend
run, validates coding/sysadmin/artifact outputs mechanically, and preserves
backend-specific telemetry in each JSON result.

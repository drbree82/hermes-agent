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

## Context-pressure follow-up

Date: 2026-09-04
Task: `long_context_distributed_evidence`
Fixture: 50 evidence files, approximately 2.4 KB each, with required facts at
the beginning, middle, and end
Model/provider: the same Inkling/OpenRouter route
Repetitions: 5 per strategy

The first version of this fixture did not create enough pressure: the model
often completed it in 6–11 calls, so adaptive mode stayed `collecting` and no
compaction occurred. That calibration run is retained in
`reasoning_benchmark_results/long-context-adaptive-20260904/`. The enlarged
fixture and a threshold of 16 messages produced the intended activation and
compaction path. The threshold is a fixture setting; the normal default stays
28 messages.

| Strategy | Success | Median total tokens | Median calls | Median time | Median capsule | Median omitted | Median net savings | Median compactions | Activation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| legacy control (adaptive batch) | 5/5 | 190,089 | 13 | 22.2s | 0 | 0 | 0 | 0 | none |
| arc_continuous adaptive | 5/5 | 163,553 | 11 | 20.7s | 1,198 | 16,269 | **+10,460** | 3 | tool_output_pressure |
| legacy control (forced batch) | 5/5 | 116,609 | 8 | 14.1s | 0 | 0 | 0 | 0 | none |
| arc_continuous forced/eager | 5/5 | 146,856 | 8 | 20.0s | 1,198 | 1,248 | **-7,013** | 1 | configured_always |

The controls are stochastic batches, so the adaptive and forced legacy rows
are not identical random samples. Within the five adaptive pairs, ARC token
deltas were `[+127,320, -96,516, +25,172, -48,457, -26,536]`; the median
paired delta was `-26,536` tokens. Within the forced pairs the median paired
delta was `+31,770` tokens. Both strategies retained the required facts and
passed the validator in every repetition.

This is evidence for an activation crossover, not a universal win:

* Below pressure, the earlier short-task suite showed that eager projection
  adds context without improving success.
* Under this fixture’s pressure, adaptive projection activated once and then
  compacted the trajectory three times at the median. It omitted 16,269
  estimated legacy-transcript tokens and added a 1,198-token capsule, yielding
  positive median net savings and lower median calls/time than its control.
* Forced/eager projection activated immediately, usually omitted almost no
  legacy history, and had negative median net savings. This isolates eager
  activation as a real cost.
* Success was 5/5 for all three measured strategies. The improvement was
  efficiency/context handling, not task correctness.
* The adaptive paired deltas varied widely, including one +127k-token run and
  one run with only +511 net savings in the earlier calibration. More tasks
  and context sizes are needed to estimate a stable crossover point.

The practical current policy is therefore: keep `legacy` behavior while a
generic task is healthy and below pressure; let `arc_continuous` collect state
without changing the request; activate it only on pressure, resume, or an
explicit experiment; and compact old active-turn pairs only after activation.

The raw v2/v3 traces are committed under:

* `reasoning_benchmark_results/long-context-adaptive-v2-20260904/` — fixture
  calibration with compaction threshold 16;
* `reasoning_benchmark_results/long-context-adaptive-v3-20260904/` — final
  adaptive comparison;
* `reasoning_benchmark_results/long-context-forced-v2-20260904/` — final eager
  comparison.

## Adaptive reliability follow-up

Date: 2026-09-04. This follow-up uses the same Inkling/OpenRouter route and
does not change the production default (`legacy`). The long-context validator
was strengthened before these runs: it now requires the complete 50-entry
inspection ledger, recomputes each evidence file's SHA-256, checks every
verified/unverified status, and checks the three anchors plus the combined
synthesis in `LONG_REPORT.md`. Results from earlier runs are not silently
reclassified under this stronger validator.

### Threshold calibration

Three paired repetitions were run for each deliberately non-Cartesian setting
on `long_context_distributed_evidence`. The trajectory-compaction threshold was
held at 28 messages for this calibration. `Δ tokens` is ARC minus legacy;
negative is better. A zero projection row means the net-benefit guard correctly
kept the healthy transcript intact.

| Activation setting (messages / tool chars / context ratio) | Valid ARC | Median Δ tokens | Median ARC calls | Median compactions | Notes |
|---|---:|---:|---:|---:|---|
| 12 / 16k / .55 | 2/3 | +7,012 | 17 | 1 | One invalid run and high variance |
| 20 / 24k / .65 | 3/3 | -76,573 | 10 | 0 | Usually never activated |
| 28 / 32k / .72 | 1/3 | +40,161 | 14 | 0 | One +191k outlier; two invalid outcomes |
| 36 / 40k / .80 | 3/3 | +50,420 | 13 | 0 | Usually never activated |

This sweep does not establish a stable crossover from activation pressure alone.
The settings that avoided projection often had the best token result simply
because they behaved like legacy; the aggressively activated setting had
mechanical failures and large tails. The useful trigger remains imminent
trajectory compaction/context pressure, combined with the net-benefit guard,
rather than an eager fixed message count.

### Twenty paired repetitions at the strongest candidate

The candidate was adaptive mode with trajectory compaction at 16 messages,
activation at 24 messages or 24k tool characters, context ratio .72, and a
1,200-token capsule. Every pair used a clean fixture copy. The full traces are
under `reasoning_benchmark_results/long-context-candidate20-20260904/`.

| Strategy | Validated success | Mean tokens | Median tokens | P25–P75 tokens | Mean calls | Median calls | Mean time | Median time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| legacy | 18/20 | 195,186 | 178,469 | 151,582–250,936 | 11.6 | 11.5 | 24.0s | 22.2s |
| arc_continuous adaptive | 15/20 | 192,680 | 170,791 | 154,166–211,800 | 13.1 | 11.5 | 26.0s | 23.8s |

Paired ARC-minus-legacy token delta had mean **-2,506**, median **+1,875**,
P25–P75 **-29,823 to +43,237**, standard deviation **72,522**, with 10 wins,
10 losses and no ties. ARC's measured duration delta had median +2.4 seconds
(P25–P75 -3.7 to +6.6 seconds). ARC had 1.9 median compactions (P25–P75
1–2.25), 5.2 median projections, and a 1,198-token capsule. The median
estimated net context saving was positive at 25,105 tokens, but that did not
translate into a reliable provider-token or success advantage.

Tail risk was material: ARC was more than 25% more expensive than its paired
legacy run in 5/20 pairs and more than 50% more expensive in 3/20; no run was
more than 100% more expensive. ARC passed the mechanical validator in 15/20
runs versus 18/20 for legacy. This candidate therefore should not replace
legacy, despite its lower mean provider-token total.

### Pathological-run diagnosis

The new `reasoning_state.continuity_trace` records activation/compaction reason,
raw and projected context sizes, capsule size, omitted tokens, net savings,
state counts, and hysteresis suppressions without recording raw reasoning.
The largest-cost candidate trace (repetition 10) shows the causal sequence:

1. A first compaction occurred at 17 messages, but only 527 estimated tokens
   were removable, so the first projection was net-neutral after the capsule.
2. The substrate then projected the active turn repeatedly while the raw tool
   stream grew; hysteresis suppressed intervening compactions until enough new
   messages or tool bytes accumulated.
3. Later compactions removed 3,881 and then 18,880 transcript tokens from the
   projected request, while retaining a 1,198-token capsule. The trace records
   five compactions/projections in that run, not twelve uninstrumented events.

The trace points to stochastic model trajectory/tool-output growth—not stale
state duplication—as the main cost driver in this run. The code now prevents
zero-growth compaction, records every suppression, keeps the last compaction's
task epoch, and refuses low-benefit activation before there is removable
history. The long coding fixture also produced a 97-call/24-compaction ARC
outlier under intentionally low thresholds; this is retained as a warning that
fixture pressure settings themselves can expose tail instability.

### Additional pressure fixtures

Two deterministic fixtures were added: `long_coding_debug` requires a repair,
full pytest validation, and all 12 distributed evidence items;
`long_operational_incident` requires a diagnosis combining 12 logs while
rejecting an unverified database-outage distractor. Initial operational runs
were invalidated by a validator wording bug, so they are retained separately
and not counted as corrected results. A corrected rerun is in
`reasoning_benchmark_results/long-incident-validator-pass3-20260904/`; it
recorded 0/3 validated outcomes for both backends. This is a real fixture
signal: one legacy run reported a failed artifact write, while one ARC report
omitted an evidence ID; the mechanical validator correctly rejected both
runs. The coding run's five paired repetitions produced 4/5 validated ARC
outcomes and 5/5 legacy outcomes; ARC had one mechanical test failure and one
97-call tail outlier. These fixtures confirm that the variance is not unique
to the 50-file evidence task, but the current small fixture sample is not a
general model-quality estimate.

### Crossover conclusion

For short tasks, eager capsules remain a clear cost with no success benefit.
For genuinely pressured tasks, ordinary transcript continuity and substrate
continuity can both succeed; substrate-managed projection can reduce the
estimated request context substantially after compaction, but this experiment
does not show a reliable total-token or success improvement. The approximate
crossover is therefore conditional rather than a single token count: it begins
only after a real compaction would otherwise be required and only when enough
history is removable to pay for the capsule. At the tested 16-message
compaction point, median context savings were positive but success and tail
cost were worse often enough to rule out promotion. No crossover point can be
claimed yet for general Hermes work.

The local OpenAI-compatible endpoint at `192.168.68.65:8086` was unavailable
on this date, so no Qwen/local-model conclusion is claimed. The next valid
step is to repeat the same fixtures with a live second Tier-2 model; no Inkling
specific tuning should be promoted before that comparison.

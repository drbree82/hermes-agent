# Long coding/debug investigation

Repair the application and inspect every file under `evidence/` in numbered
batches before deciding what to change. The evidence is deliberately spread
across the investigation: early files establish the input contract, middle
files identify the regression, and late files describe the failure that must
be tested. Some notes are hypotheses; verify them against the code and tests.

The repaired application must pass `pytest -q`. Write `REPAIR_TRACE.md` with
the exact lines `INPUT_CONTRACT=stable-v2`, `REGRESSION=cursor-reset`, and
`LATE_FAILURE=empty-batch`, explain the fix, and list every evidence ID
(`001` through `012`) that was inspected. Do not claim an evidence item was
verified solely because of its filename.

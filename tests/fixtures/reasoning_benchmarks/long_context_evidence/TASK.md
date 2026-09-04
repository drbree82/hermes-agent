# Long-context evidence investigation

Inspect every file under `evidence/` using the terminal. Read the files in
batches so the investigation has many sequential observations. Some files are
distractors and some facts conflict; trust only statements marked `VERIFIED`.

Write `LONG_REPORT.md` containing:

1. The verified early anchor exactly: `ANCHOR=ORBIT-7`.
2. The verified middle control exactly: `CONTROL=MIDDLE-42`.
3. The verified late checksum exactly: `CHECKSUM=LATE-91`.
4. A concise explanation of how the three facts combine, a list of inspected
   evidence files, and any unresolved questions.

The validator checks facts from the beginning, middle, and end of the
trajectory. Do not manufacture or infer a value when a fragment is marked
`UNVERIFIED`.

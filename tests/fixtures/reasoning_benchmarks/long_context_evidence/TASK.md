# Long-context evidence investigation

Inspect every file under `evidence/` using the terminal. Read the files in
batches so the investigation has many sequential observations. Some files are
distractors and some facts conflict; trust only statements marked `VERIFIED`.

Write `INSPECTION_LEDGER.json` as a JSON array with exactly 50 entries, one
for every evidence file. Each entry must contain the zero-padded `id`, the
SHA-256 digest of that file's complete contents as `sha256`, and its verified
status (`VERIFIED` or `UNVERIFIED`) as `status`. Include a short extracted
observation for each entry. The ledger is the mechanical proof that every
fragment was processed; do not list only the anchor files.

Write `LONG_REPORT.md` containing:

1. The verified early anchor exactly: `ANCHOR=ORBIT-7`.
2. The verified middle control exactly: `CONTROL=MIDDLE-42`.
3. The verified late checksum exactly: `CHECKSUM=LATE-91`.
4. A concise explanation containing the word `combined`, a list of inspected
   evidence files, and any unresolved questions. Do not call any known
   `UNVERIFIED` distractor a verified fact.

The validator checks facts from the beginning, middle, and end of the
trajectory. Do not manufacture or infer a value when a fragment is marked
`UNVERIFIED`.

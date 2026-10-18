# Checkpoint provenance

`checkpoint_before.json` / `checkpoint_after.json` were produced by run **final-100k** (interrupted gracefully after
saved progress, then resumed — see the recording linked in the README and `scripts/resume_proof.py final-100k`).
Run final-150k is a strict superset: it imported every final-100k result and its original call record (including
`phase: "resume"`) through the cross-run cache, so the same checkpoints are valid against `grading/records.jsonl.gz`
and `grading/calls.jsonl.gz`.

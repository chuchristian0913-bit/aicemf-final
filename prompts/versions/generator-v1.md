# pcd-generator (v1 — superseded; kept as the evolution baseline)

Draft the note from the verified metric registry.

## Output
`cases/<case>/rounds/<round>/note/claims.json`, plus the script that built it.
Every claim carries `metric_id` and `quoted_value` equal to the registry value —
build the claims file from the registry programmatically so transcription errors
cannot exist.

## Rules
- Every number in prose must be a claim with a `metric_id`; a decimal or
  percentage in a sentence that no claim produced is a gate failure even when
  it happens to be right.
- Disclose every finding with `requires_note_disclosure=true` that applies to a
  metric you cite: a `limitations` entry, a footnotes block key, and a footnote
  reference in the comparative prose.
- Never estimate a missing input. Print `n/a` and let the finding explain why.
- Write only inside the round directory declared in your request.

## Known gaps (why v2 exists)
v1 does not require the note to disclose mixed fiscal year-ends, and does not
constrain table column specs. Both gaps produced real failures; see v2.

# pcd-generator (v2 — active)

Draft the note from the verified metric registry.

## Output
`cases/<case>/rounds/<round>/note/claims.json`, plus the script that built it.
Every claim carries `metric_id` and `quoted_value` equal to the registry value —
build the claims file from the registry programmatically so transcription errors
cannot exist.

## Rules
1. Every number in prose must be a claim with a `metric_id`; a decimal or
   percentage in a sentence that no claim produced is a gate failure even when
   it happens to be right.
2. Disclose every finding with `requires_note_disclosure=true` that applies to a
   metric you cite: a `limitations` entry, a footnotes block key, and a footnote
   reference in the comparative prose.
3. **Period alignment (added after the round-1 rejection):** if the panel mixes
   fiscal year-end months, the note must carry a `period_alignment` limitation,
   reference it (`[[period_alignment]]`) in the sentence that makes the cross-
   company comparison, and list it in the footnotes block. A peer table that
   puts a September year-end next to December year-ends without saying so is
   comparing two different economies and calling it growth.
4. **Table integrity (added after the publish-time latexmk failure):** every
   table's `spec` must declare exactly as many columns as its `columns` list.
   G01 validates this; a mismatch is a submission rejection, not a rendering
   detail.
5. Never estimate a missing input. Print `n/a` and let the finding explain why.
6. Write only inside the round directory declared in your request.

## Change record
- v1 → v2: rules 3 and 4. Rule 3 answers the seeded G11 rejection of round 1;
  rule 4 answers the publish-time table-spec failure archived in
  `cases/case-01/archive/published-run-broken-pdf/`. Both changes were carried
  to the role as findings; neither weakens any acceptance criterion.

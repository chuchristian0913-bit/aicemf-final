# Recovery evidence

Interruptions, rejections and post-publication defects — each handled through the
controller, each with artifacts left in the repository.

## 1. Round-1 rejection → repair in place (gate-driven)

- Attempt 1: G11 `period_alignment` FAIL — the seeded narrative defect (Visa's
  September year-end next to four December year-ends, unmentioned).
  Evidence: `cases/case-01/archive/first-run/round-001-attempt1/gates_generator_attempt1.json`.
- The role stayed in place (retry counter 1/2), the carried finding produced the
  `period_alignment` limitation + footnote reference + footnotes block entry, and
  attempt 2 passed G11.

## 2. Observed-write true positive (G12)

The controller hashes the tree before dispatch and after submission. Attempt 2 was
rejected on G12 because `src/pcd/cli.py` changed inside the run window — a **true
positive**: the controller's own source was edited mid-run. It was recorded as such
(`retry_counter_reset` trace event naming the controller-side defect), not suppressed.
The write detector's exclusion pattern was also hardened from `endswith` to a regex
after `gates_generator_attempt1.json` slipped past it. Evidence: archived trace.

## 3. Interrupted deterministic chain → byte-identical resume

With state at `MODELED`, both work outputs were deleted (simulated interruption/loss):

```
$ python -m pcd.cli status      → resumable: false
                                  problem: output_missing cases/case-01/work/standardized.json
                                  problem: output_missing cases/case-01/work/metrics.json
$ python -m pcd.cli resume      → STANDARDIZED (recomputed)
                                  MODELED (recomputed)
                                  deterministic outputs restored; dispatching
```

SHA-256 of all three work outputs compared before deletion and after recovery:
**byte-identical** (`RECOVERY` transcript in this file's companion commit message;
fingerprint skip logic unit-tested in `tests.test_state.TestRunStage`).

A real defect was found and fixed here: the first version of `resume` dispatched the
generator past the missing registry. The fix routes `MODELED` + `output_missing`
through the deterministic re-run first (commit `50dda1e`).

## 4. Publish-time defect → archive, patch, full re-run

The first complete run **published** a note whose DCF table declared six columns under
a five-column spec. Every gate passed; latexmk failed at publish. The run was archived
intact (`cases/case-01/archive/published-run-broken-pdf/` — state, trace, rounds,
approval, decision, result, and the failed `latex_error.log`), the gate suite was
extended (G01 v1.1 validates the note artifact), and the fixed candidate
`e8e5200a79a0` went through the complete cycle again: 13/13 gates, three clean critic
reports, two consecutive clean reviews, approval, publish with a compiling 2-page PDF.

## 5. Snapshot integrity after restart

`prepare` adopts an existing frozen snapshot only after re-hashing every stored payload
against `MANIFEST.json` (`sources.verify_snapshot`); a mismatch is a stop condition.
Both replays adopted the snapshot with `verified=True` (trace event
`snapshot_adopted`), avoiding a re-fetch of ~19 MB of EDGAR payloads.

## 6. Version rollback availability

The evolution registry keeps every version and never deletes. The drill
(v1.1 → v1.0 → v1.1) is recorded with reasons and operator in
`config/versions/registry.json` history and in
`evidence/evolution/cycle-G01-v1.0-to-v1.1.json`.

## 7. Nothing was fixed by editing history

Every failure above is preserved: two archives (`first-run/`,
`published-run-broken-pdf/`), attempt-level gate files, trace events, and commits that
describe the defect each change answered. The published case itself
(`cases/case-01/`) contains only the clean, accepted lineage.

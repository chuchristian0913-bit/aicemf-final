# Verification evidence

Every claim below is reproducible from the repository. Commands assume the repo root
with `PYTHONPATH=src` and the Python 3.13 interpreter.

## 1. The acceptance gates — 13 executable criteria

```bash
python -m pcd.cli gates --round round-002
```

Result on the published candidate `e8e5200a79a0`: **13/13 PASS**, zero blocking
findings. The full bundle with per-gate evidence blocks is stored at
`cases/case-01/rounds/round-002/gates_generator.json` and `gates_review.json`.

## 2. Gates can actually fail (a gate that cannot fail is decoration)

`python -m unittest tests.test_gates` runs mutation tests that inject each gate's
target defect into a copy of the real candidate and assert rejection:

| Mutation | Gate | Result |
|---|---|---|
| Prose number `17.3%` no claim produced | G07 | FAIL `unreferenced_number` |
| Signed percentages `-1.7%`/`+2.1%` kept whole | G07 | FAIL (one token each) |
| Footnote exists but prose never cites it | G11 | FAIL `footnote_not_referenced` |
| Limitation removed entirely | G11 | FAIL `missing_footnote` |
| DCF table: 6 columns under 5-column spec | G01 (v1.1) | FAIL `table_spec_mismatch` |
| Footnote ref to a non-existent limitation | G01 | FAIL `footnote_ref_without_limitation` |
| Extra submission key | G01 | FAIL `extra_keys` |
| Ticker literal in model.py | G10 | guarded (no literal present → PASS) |

Historical evidence of gates rejecting real submissions:
`cases/case-01/archive/first-run/round-001-attempt1/gates_generator_attempt1.json`
— **G11 FAIL** on the seeded period-alignment defect (attempt 1), repaired in place.

## 3. The leakage probe is live

`tests.test_gates.TestG13Leakage` asserts both halves of G13 on the real snapshot:
post-cutoff perturbation signature **==** baseline (no future information leaked) and
pre-cutoff perturbation signature **!=** baseline (the probe would detect a leak).

## 4. Independent recomputation (critic phase)

```bash
python scripts/critic_checks.py round-002
```

Three specialists, zero findings on the published candidate:

- **tieout** — margins and growth recomputed from `standardized.json` source values
  (not from `metrics.json`), all 50 `quoted_value`s match the registry exactly, CAGR
  windows respect the GPN recast.
- **provenance** — all 5 frozen payloads re-hashed against `MANIFEST.json`; every
  source reference resolves and is filed ≤ cutoff; disclosure obligations covered.
- **model-review** — DCF rebuilt from `config/assumptions/case-01.toml` with
  independent arithmetic; sensitivity grid monotone in both axes; Visa's per-share
  correctly withheld with finding `F-SHARES-V`.

Reports: `cases/case-01/rounds/round-00{1,2}/critic/*.json`.

## 5. Two consecutive clean reviews of the same candidate

`round-001/verdict.json` → verdict `clean`, streak 1/2 → REPEAT.
`round-002/verdict.json` → verdict `clean`, streak 2/2 on the **same candidate hash**
`e8e5200a79a0…` → ACCEPT. Recorded in `cases/case-01/decision.json` and the trace.
`tests.test_state` pins the routing semantics (changed candidate resets the streak;
exhaustion never accepts; empty approver rejected).

## 6. Human approval before the consequential action

`cases/case-01/approval.json`: approver "Christian", decision approve, scope bound to
the candidate hash, authority `human-analyst`. `cmd_publish` refuses without it;
`record_approval` rejects an empty approver (unit-tested).

## 7. The regression suite

```bash
python -m unittest discover -s tests
```

**70 tests, all passing** — utilities, renderer contracts (including the
marker-before-escaping regression), controller semantics, gate mutations, leakage
probe, evolution cycle (QT guard, fixed-case evaluation, registry, rollback).

## 8. Evolution on a fixed baseline

`evidence/evolution/cycle-G01-v1.0-to-v1.1.json`: diagnosis (real publish-time
failure, twice), scoped patch, QT guard pass (15 protected knobs untouched),
evaluation on 2 dev + 2 held-out cases (v1.0 catches 1/4, v1.1 catches 4/4),
70-test regression gate, owner approval, version registry with implementation
hashes, rollback drill (v1.1 → v1.0 → v1.1, all events recorded).

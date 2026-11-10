---
name: agent-run-forensics
description: Diagnose a failed gate / rejected submission / broken publish in an agent workflow run, using the case audit trail instead of re-running blindly.
when_to_use: A controller rejects a submission, a gate fails, or a run stops in a terminal state and you need the why from evidence.
---

# Run forensics from the case audit trail

1. **Read state, not memory.** `cases/<case>/state.json` (stage, retries, candidate hash)
   and `trace.jsonl` (every transition, gate run, write event). The trace is append-only;
   the last line is where it stopped.
2. **Gate failures**: open `rounds/<round>/gates_*.json` — each gate returns
   PASS/FAIL/NOT_CHECKED with a `failures` list carrying `kind` and evidence. `kind` is
   the diagnosis vocabulary: `unreferenced_number`, `table_spec_mismatch`,
   `undeclared_write`, `post_cutoff_admitted`, `probe_insensitive`, `future_information_leaked`…
3. **G12 undeclared writes** — distinguish three cases:
   (a) controller-owned artifact false positive → widen the exclusion REGEX (a plain
   `endswith` let `gates_generator_attempt1.json` slip through);
   (b) a real role write outside declared artifacts → true finding, do not suppress;
   (c) the controller itself edited source inside the run window → true positive,
   record `retry_counter_reset` in the trace and own it.
4. **G13 vacuous probe** — if `probe_insensitive` or `future_information_leaked`, check
   the probe used the issuer's REAL fiscal period (Visa ends in September; a calendar
   year-end probe is ignored by the resolver, making both gate halves vacuous).
5. **Publish-time failures** are workflow failures: if latexmk failed, the defect passed
   every gate — find the missing gate, file it as an evolution proposal (dev case from
   the real defect, held-out cases untouched, QT guard, owner approval, registry).
6. **Never fix by editing history.** Archive the broken run
   (`cases/<case>/archive/<name>/`), fix forward, replay. The archive IS the recovery
   evidence.

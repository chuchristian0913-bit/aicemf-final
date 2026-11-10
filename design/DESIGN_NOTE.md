# Design Note — Payments Coverage Desk (PCD)

**Student:** 25210660135 (Christian, `chuchristian0913-bit`)
**Course:** AI Coding in Finance, Fall 2026 — Final Project
**Task:** Design a long-running agent workflow for a finance task

---

## §1 — The task, and why it needs a persistent workflow

### 1.1 The recurring task

The desk covers five global payments companies — **Visa (V), Mastercard (MA), PayPal (PYPL), Fiserv (FI), Global Payments (GPN)** — and produces an annual peer-panel update when earnings season closes. The user is the sell-side analyst of record (me); the deliverable is a two-page peer comparison note with a DCF cross-check, published internally each year.

The cycle **restarts on a filing event**: the last 10-K among the universe filed at or before the information cutoff triggers the run (`config/run.toml [trigger]`). A monthly fallback (first business day after the 20th) catches silent cases. Each cycle must be comparable with the previous one **without rewriting history**: last year's note quoted last year's numbers, and this year's restatements may not silently replace them.

### 1.2 Stages and dependencies

```
freeze sources → standardise → model → draft → gate → review (×2 clean) → approve → publish → archive
```

Dependencies are strictly linear on the deterministic side (freeze → standardise → model), then role-driven: the generator needs the model registry; the critics need the frozen snapshot and the draft; the controller needs gate results and critic reports; publish needs everything plus a named human approval.

### 1.3 Inputs, permitted sources, cutoffs

- **Permitted sources:** SEC EDGAR XBRL companyfacts only (`src/pcd/sources.py` is the single module allowed to open a socket). No market prices — the note is explicitly not a price-target exercise. Forward-looking numbers come exclusively from the versioned assumption file (`config/assumptions/case-01.toml`).
- **Information cutoff:** `2026-04-30`, fixed in the mandate before the run. The freeze **deliberately keeps post-cutoff rows** in the raw payloads so gate G04 can prove the cutoff filter actually rejects something (a filter that rejected nothing proves nothing).
- **Missing inputs are declared, never invented:** Visa does not tag a diluted share count; its per-share value is published as `n/a` with finding `F-SHARES-V` and a footnote. Zero is a number; `n/a` is an admission (§2.6).

### 1.4 Work products and acceptance criteria

| Work product | Where |
|---|---|
| 2-page PDF/HTML note, every figure bound to a `metric_id` | `output/note.pdf`, `output/note.html` |
| Machine-readable panel | `output/panel.json`, `output/panel.csv` |
| Auditable claim resolution table | `output/claim_resolution.json` |
| Full audit trail | `cases/case-01/state.json`, `trace.jsonl`, `rounds/` |

**Acceptance** = all 13 gates PASS + no open high-severity finding + **two consecutive clean full reviews of the same candidate** + recorded approval by a named human. Research acceptance establishes internal consistency, not profitability, and authorises nothing beyond publication.

### 1.5 Why not a one-shot script

A script would re-run everything on every invocation, could not resume mid-run after an interruption, has no notion of a candidate being rejected and repaired *in place* with an audit trail, cannot enforce "no network after the freeze", and conflates producing and checking in one process. The task is long-running (multiple rounds, human approval in the middle, wall-clock limits), stateful (findings carried across rounds), and adversarial to its own producer (gates exist because the generator is not trusted). Sections 2.1–2.3 are each impossible in a one-shot script.

---

## §2 — Architecture

### 2.1 Roles and handoffs

Nine roles, each with a contract (`config/roles.toml`): declared tools, write scope, inputs, outputs, and explicit `cannot` lists. Handoffs are **requests and submissions**, not chat: the controller writes `request.json` (what to produce, under what limits, with which carried findings); the role writes a `submission.json` answering that exact `request_id` — the controller rejects a submission that answers a stale request.

| Role | Decides | Produces | Cannot |
|---|---|---|---|
| `pcd-orchestrator` | routing, retries, stop | requests, state transitions | produce content, run gates |
| `pcd-generator` (lead) | narrative, claim selection | `note/claims.json` + authoring script | advance state, pass itself |
| `pcd-sources` / `pcd-model` / `pcd-note` | — | sub-artifacts of the draft | write outside `cases/<case>/` |
| `pcd-critic` (lead) | verdict routing input | consolidates specialist reports | edit the candidate |
| `pcd-tieout` / `pcd-provenance` / `pcd-model-review` | specialist findings | `critic/*.json` | write the candidate, see each other's reports |
| `human-analyst` | approve / reject / defer | `approval.json` | be simulated by an agent |

**Deterministic code computes; agents decide.** Ratios, CAGRs, the DCF and the sensitivity grid are computed by `src/pcd/model.py`, which contains **no ticker literals** (gate G10 enforces this — a judgement that escaped into code must live in the assumption file). The agent drafts prose and chooses which claims to make; it does not compute. The critics re-derive, they do not proofread: `scripts/critic_checks.py` recomputes margins from standardized source values, re-hashes the frozen snapshot, and rebuilds the DCF from the assumption file with independent arithmetic.

**Hooks vs controller.** A hook is an event callback; the controller is the workflow authority. Observed writes are the sharpest example: the controller hashes the tree before dispatch and after submission and classifies every change as declared or undeclared — "the agent said it only wrote two files" is testable, and in the first archived run the detector produced a **true positive** (a controller-side source edit inside the run window), which was recorded as such rather than suppressed.

### 2.2 Long-running operation

- **Persistent state.** `state.json` is written atomically at every transition; the trace (`trace.jsonl`) records transitions, stage completions, gate runs, writes and approvals.
- **Content-addressed checkpoints.** `run_stage()` fingerprints inputs and skips a stage whose fingerprint matches existing outputs — a resume never repeats completed work, and changed inputs record `stage_invalidated` instead of silently recomputing.
- **Resume without re-doing.** Demonstrated: with state at `MODELED`, both work outputs were deleted; `status` reported `output_missing`; `resume` recomputed both stages **byte-identically** (hash-compared) before dispatching. An earlier version of `resume` dispatched the generator past the missing registry — fixed, and the fix has its own commit and rationale.
- **Retries and stop rules.** Transient errors back off (2 s, 8 s); a retry never re-runs a hash-matched stage. `max_rounds 3`, `max_retries 2`, tool/LLM/wall-clock budgets. `REVIEWED → EXHAUSTED` on round exhaustion — **exhaustion never grants acceptance**; `BLOCKED`, `STOPPED`, `INSPECT_REQUIRED` are distinct non-success terminals.
- **Permission limits.** Network only during `SOURCES_FROZEN`; writes confined to `cases/` and `output/`; `data/frozen/`, `config/`, `src/`, `prompts/`, `.git/` are forbidden to roles.

### 2.3 Verification and improvement

**Checks are independent of the producer.** The 13 gates (`config/gates.toml`, `src/pcd/gates.py`) are functions of files, never of intentions. Four worth reading first: G07 tie-out (every number reached through a `metric_id`; a quoted number with none is an automatic FAIL), G10 model integrity (DCF re-derived from assumptions; ticker literal in `model.py` is a failure), G11 period alignment (a panel mixing September and December year-ends must say so — the seeded defect of round 1), G13 leakage.

**G13 is a probe that can actually fail.** Two halves: a post-cutoff perturbation must change nothing (no future information leaked), and a pre-cutoff perturbation must change something (the probe is live). Its first version probed with a calendar year-end the resolver ignored for Visa's September fiscal year, making **both** halves vacuous — the bug and its fix are documented in the gate's docstring and commit.

**Human approval before consequential action.** `publish` refuses without a recorded approval; `record_approval` rejects an empty approver ("the agent is not an approver"). Approval is bound to the candidate hash.

**Bounded revision loop.** Gate rejection keeps the role in place with a retry counter; review findings carry into the next round; `route_after_review` requires two consecutive clean reviews **of the same candidate hash** — a changed candidate resets the streak, so a repair cannot inherit its predecessor's cleanliness.

**Evolution on a fixed baseline.** `src/pcd/evolution.py` + `scripts/evolution_demo.py` run the full cycle on a real recurrence: G01 v1.0 (envelope-only) missed a note-artifact defect twice, both times failing at publish time inside latexmk. The patch (v1.1: `validate_note_schema`) was evaluated on **development cases** built from the real defect and on **held-out cases** that ran once for assessment only; the 70-test regression suite passed; a named owner approved; the registry (`config/versions/registry.json`) records both versions with implementation hashes; a rollback drill re-pointed the active version and rolled forward. **QT guard:** a proposal touching limits, tolerances, the leakage probe, stop rules, the transition map or permissions is rejected outright — a change cannot be judged by the acceptance it redefines.

### 2.4 What the real data taught the architecture

Building against live EDGAR surfaced four defects that a synthetic example would have hidden, each of which now drives a mechanism:

1. **FI tag drift** — Fiserv switched D&A concepts mid-series ($3.2 bn → $1.7 bn with no economic event). Drives tag-resolution priority, `series_breaks`, and G05's disclosure requirement, scoped to **cited** concepts (demanding footnotes about uncited concepts trains boilerplate).
2. **GPN recast** — FY2023 revenue re-presented −23.6% after divestitures. Drives comparability propagation, CAGR window restriction (FY2023–25 only), and G06.
3. **V missing share tags** — per-share withheld as `not_available` (§2.6 doctrine) rather than estimated.
4. **MA `ProfitLoss` vs `NetIncomeLoss`** — a single-concept resolver would have erased one of five names from the panel; `fiscal_ends()` anchors on the modal annual period across all flow facts, not one concept.

### 2.5 Mandate immutability

`config/run.toml` is hashed into state at init (`mandate_hash`). Changing the cutoff, universe or limits is a **new mandate**, not an edit: published cases are never silently recomputed (`reopen_previous = false`).

### 2.6 The missing-input doctrine

`safe_div` returns `None` on a zero denominator; the model emits `not_available` records with findings attached; the renderer prints `n/a`; G03 distinguishes an explicit `not_available` from an invented zero ("zero where zero is implausible" is a failure). The system's most useful sentence is "we do not estimate an input we cannot source."

---

## §3 — Repository guide

See `README.md` for the file guide, setup and run walkthrough. Evidence map:

| Claim | Evidence |
|---|---|
| Gates can fail | `tests/test_gates.py` mutation tests; archived `gates_generator_attempt1.json` (G11 FAIL) |
| Review loop is bounded and streak-reset | `tests/test_state.py`; `cases/case-01/rounds/round-00{1,2}/verdict.json` |
| Resume is safe and cheap | recovery demo in `evidence/RECOVERY.md`; byte-identical hash comparison |
| Leakage probe is live | `tests/test_gates.py::TestG13Leakage`; G13 evidence block in every gate run |
| Writes are observed, not claimed | `trace.jsonl` write events; the archived G12 true positive |
| Evolution cycle | `evidence/evolution/cycle-G01-v1.0-to-v1.1.json` |
| Human approval required | `cases/case-01/approval.json`; `tests/test_state.py::TestApproval` |

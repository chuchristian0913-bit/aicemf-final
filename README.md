# Payments Coverage Desk (PCD)

A resumable, auditable **long-running agent workflow** for a recurring sell-side
research task: the annual peer-panel update for five global payments companies
(Visa, Mastercard, PayPal, Fiserv, Global Payments), built exclusively from a
frozen SEC EDGAR XBRL snapshot, gated by 13 executable acceptance criteria, and
published only with a named human approval.

**Student:** 25210660135 (Christian) · **Git:** `chuchristian0913-bit` /
`chuchristian0913@gmail.com` · **Course:** AI Coding in Finance, Fall 2026, FISF Fudan

**Repository:** https://github.com/chuchristian0913-bit/aicemf-final.git

---

## The one-line design

Deterministic code computes; an agent drafts; *different* agents recompute from the
frozen files; a named human approves publication. State, hashes and findings persist
at every transition, so an interrupted run resumes without repeating work — and a
published case is never silently recomputed.

## Design note and diagram

- `design/DESIGN_NOTE.md` — full design note mapped to the assignment's §1/§2/§3
  (task, roles/handoffs, long-running operation, verification & improvement, and
  what live EDGAR data taught the architecture).
- `design/workflow_diagram.svg` — annotated diagram of one research cycle.

## File guide

```
config/
  run.toml                  the mandate: case, trigger, limits, retry, stop rules, permissions
  roles.toml                9 role contracts (tools, write scopes, cannot-lists)
  gates.toml                the 13 gates with tolerances
  assumptions/case-01.toml  ALL forward-looking numbers (versioned, analyst-owned)
  versions/registry.json    evolution registry (versions, impl hashes, rollback history)
src/pcd/
  sources.py                the ONLY module allowed to open a socket (EDGAR freeze + verify)
  standardize.py            tag resolution, cutoff filter, drift/recast/break detection
  model.py                  deterministic ratios, CAGR, DCF, sensitivity — zero ticker literals
  gates.py                  G01–G13, functions of files, never of intentions
  state.py                  controller: stages, content-addressed checkpoints, routing, approval
  report.py                 claims-driven renderer (LaTeX + HTML); every number resolves
  cli.py                    operator commands: init/prepare/request/submit/review/approve/publish/status/resume
  evolution.py              scoped version cycle + QT guard + registry + rollback
prompts/                    role prompts as contracts; versions/ keeps generator v1 (superseded)
skills/                     3 reusable skills (EDGAR freeze, claims-driven rendering, run forensics)
tests/                      70 tests incl. gate mutation tests, leakage-probe liveness, evolution
  cases/g01/                development cases + held-out cases for gate evaluation
scripts/
  critic_checks.py          the three specialist critics as executable, reproducible checks
  evolution_demo.py         executes the G01 v1.0→v1.1 evolution cycle end to end
cases/case-01/
  work/                     standardized.json, sources.jsonl, metrics.json (405 metrics)
  rounds/round-00{1,2}/      submissions, authoring scripts, gate bundles, critic reports, verdicts
  approval.json / result.json / trace.jsonl / state.json
  archive/                  first-run (G11 rejection, G12 true positive) and the
                            published-run-broken-pdf run — recovery evidence, kept intact
data/frozen/case-01/        the immutable EDGAR snapshot (MANIFEST + sha256 per payload)
output/                     note.pdf (2 pages), note.html, panel.csv/json, claim_resolution.json
evidence/
  VERIFICATION.md           how every acceptance claim is reproduced
  RECOVERY.md               interruptions, rejections, and the post-publication defect
  evolution/                the executed G01 v1.0→v1.1 cycle bundle
```

## Setup

Requirements: Python 3.13, a TeX Live with `latexmk` (for the PDF), no other
dependencies (tests use `unittest` from the standard library; EDGAR access needs an
outbound network only during the freeze stage — already frozen in this repo).

```bash
# from the repository root
set PYTHONPATH=src           # bash: export PYTHONPATH=src
python -m pcd.cli status     # where is the run?
python -m unittest discover -s tests    # 70 tests
```

## Run walkthrough (the cycle that produced the published note)

```bash
python -m pcd.cli init                       # state INIT, mandate hashed
python -m pcd.cli prepare                    # adopt verified snapshot → standardise → model
python -m pcd.cli request                    # dispatch the generator (request_id bound)
#   generator writes rounds/round-001/note/ via its authoring script
python -m pcd.cli submit                     # controller: observed writes + 13 gates
python scripts/critic_checks.py round-001    # three independent specialist reports
python -m pcd.cli review                     # verdict clean → REPEAT (streak 1/2)
python -m pcd.cli request                    # dispatch round 2, SAME candidate
#   generator resubmits unchanged; critics re-run
python -m pcd.cli submit && python scripts/critic_checks.py round-002
python -m pcd.cli review                     # ACCEPT (2 consecutive clean rounds)
python -m pcd.cli approve --by "Christian" --decision approve
python -m pcd.cli publish                    # refuse-without-approval; writes output/
```

Interrupted? `python -m pcd.cli resume` recomputes missing deterministic outputs
(byte-identically) before dispatching anything. Gated? the gate bundle in the round
directory names the defect and the controller routes the repair.

## Example inputs and outputs (shareable)

- **Inputs:** `config/run.toml` (mandate), `config/assumptions/case-01.toml`
  (assumption version `assumptions-2026.04`), `data/frozen/case-01/` (the snapshot).
- **Outputs:** `output/note.pdf` + `note.html` (the published note),
  `output/panel.csv` (machine-readable panel), `output/claim_resolution.json`
  (every printed number → metric_id → accession), `cases/case-01/result.json`.

Headline numbers produced by the run (FY2025): revenue $40.0bn/$32.8bn/$33.2bn/
$21.2bn/$7.7bn (V/MA/PYPL/FI/GPN); equity DCF $391bn/$315bn/$81bn/$70bn/$29bn;
Visa's per-share value withheld (`not_available`) because the issuer does not tag a
diluted share count — we do not estimate an input we cannot source.

## Dependencies

- Python ≥ 3.11 (stdlib `tomllib`, `unittest`; developed on 3.13)
- TeX Live with `latexmk` + `pdflatex` (only for `publish` with PDF)
- Outbound HTTPS to `data.sec.gov` only if re-freezing a new case (already frozen here)

## Own contribution

All architecture, code, prompts, tests, gates and documents in this repository were
designed and written by me (Christian, 25210660135). AI coding agents were used as
implementation accelerators under the workflow's own discipline — the controller,
gates, critics and mutation tests exist precisely so that agent output is never
trusted beyond what executable checks verify. Every defect surfaced during
development (gzip handling, tag drift, vacuous probes, undeclared writes, table
specs, resume routing, marker escaping) is documented with its fix in commit
history and in `evidence/`. Data: public SEC EDGAR filings; no market data; no
non-public information.

## Reproducing the verification evidence

See `evidence/VERIFICATION.md` (gates, mutation tests, leakage probe, critic
recomputation, review streak, approval binding, regression suite, evolution cycle)
and `evidence/RECOVERY.md` (rejections, true-positive write detection, byte-identical
resume, the archived publish-time failure and the re-run).

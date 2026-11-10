# AGENTS.md — Payments Coverage Desk (PCD)

Project conventions for coding agents working in this repository.

## What this repository is

A controlled, resumable agent workflow that produces a **quarterly earnings-season
coverage update** for a five-name global payments universe, from frozen SEC EDGAR
XBRL snapshots. It is a *harness*, not a chat prompt: the numbers are computed by
deterministic Python, the narrative is drafted by an agent, and an independent
critic recomputes everything before a named human may publish.

## Folder roles

| Path | Role | Writable by |
|---|---|---|
| `data/frozen/<case>/` | Immutable dated source snapshot + manifest | `pcd-sources` only, once per case |
| `cases/<case>/` | Run state, per-round artifacts, findings, decision | controller only |
| `output/` | Published tables, note (LaTeX/HTML/PDF) | publisher stage only |
| `config/` | Run limits, role cards, gate thresholds, model assumptions | human, via Git |
| `prompts/` | Role prompts, with `versions/` for the evolution registry | human, via Git |
| `skills/` | Reusable instruction packs the roles load on demand | human, via Git |
| `src/pcd/` | Controller, gates, model, adapters | the developer |
| `tests/` | Gate, leakage, resume and evolution tests | the developer |

## Hard rules

1. **Never edit `data/frozen/`.** A different snapshot is a different case.
2. **Never write outside `cases/<case>/`** during a run. The controller enforces this.
3. **No network after `SOURCES_FROZEN`.** Only `src/pcd/sources.py` may open a socket.
4. **No ticker literals in `src/pcd/model.py`.** Every company-specific number lives in
   `config/assumptions/`. Gate `G10` fails the build if this is violated.
5. **Every published number carries a `metric_id`** that resolves to a source record
   with an accession number and a filing date at or before the case cutoff.
6. **Do not invent a value for a missing input.** Emit `not_available` with a reason.
   Zero is a number; `n/a` is an admission.
7. **Consequential actions need a human.** Publishing and any change to the acceptance
   standard require a recorded approval, never an agent's own verdict.

## Style

- Python 3.11+, standard library only. No third-party imports in `src/`.
- All money in USD, all ratios as decimal fractions, all dates ISO 8601.
- Canonical JSON (sorted keys, no whitespace) for anything that gets hashed.

# Payments Coverage Desk (PCD)

A resumable, auditable agent workflow for a recurring sell-side coverage task:
the **quarterly earnings-season update** for a five-name global payments universe,
built only from frozen SEC EDGAR XBRL snapshots.

> Status: in development. The full README (setup, walkthrough, file guide,
> contribution statement) lands with the first complete case run.

## The one-line design

Deterministic code computes; an agent drafts; a *different* agent recomputes from the
frozen files; a named human approves publication. State, hashes and findings are
persisted at every transition so an interrupted run resumes without repeating work.

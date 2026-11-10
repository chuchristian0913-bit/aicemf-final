# Prompts

Role prompts are contracts, not personality. Each prompt states what the role
produces, what it may read and write, and what it is forbidden to do — the
controller enforces the boundaries with permissions and gates, so a prompt that
claims more authority than `config/roles.toml` grants is dead text.

Versioning: a prompt change that responds to a recurring failure is part of the
evolution cycle (see `src/pcd/evolution.py`). `versions/` keeps the history;
`generator-v2.md` differs from v1 by exactly the carried finding of round 1
(period alignment), which is the same cycle documented in
`evidence/evolution/cycle-G01-v1.0-to-v1.1.json` on the gate side.

A prompt may never restate an acceptance criterion as negotiable, and may never
grant write access the role does not have in `roles.toml`.

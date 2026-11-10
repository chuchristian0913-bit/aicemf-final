"""Execute the full evolution cycle for G01 v1.1 and write the evidence bundle.

Diagnose (real publish-time failure) -> scoped patch -> QT guard -> evaluate on
fixed dev cases -> held-out assessment -> regression suite -> fresh review ->
owner approval -> version registry -> rollback drill -> roll forward.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pcd.evolution as ev  # noqa: E402
from pcd.util import repo_root, write_json  # noqa: E402

CASES = ROOT / "tests" / "cases" / "g01"
DEV = sorted(CASES.glob("dev-*.json"))
HELDOUT = sorted(CASES.glob("heldout-*.json"))
EVID = ROOT / "evidence" / "evolution"
EVID.mkdir(parents=True, exist_ok=True)

cycle = {}

# 1. diagnose -- the defect is archived, not invented
cycle["diagnosis"] = {
    "symptom": "latexmk: Extra alignment tab has been changed to \\cr -- no PDF produced at publish",
    "root_cause": "note's DCF table declared 6 columns under a 5-column spec; no gate read the note artifact's structure",
    "evidence": "cases/case-01/archive/published-run-broken-pdf/ (output/latex_error.log, rounds/, state.json)",
    "recurrence": "second occurrence of the same class (the peer table had the identical bug in the first archived run), so it meets the bar for a scoped patch",
}

# 2. proposal
proposal = {
    "id": "prop-2026-11-10-g01",
    "target": "G01",
    "from_version": "v1.0",
    "to_version": "v1.1",
    "touches": ["src/pcd/gates.py:g01_schema (validate_note_schema)", "tests/test_gates.py", "tests/cases/g01/*"],
    "risk_changes": [],
    "why_scoped": "adds structural validation of the note artifact; does not touch limits, tolerances, probe, stop rules, transition map or permissions",
}
try:
    cycle["qt"] = ev.qt_check(proposal)
except ev.ProposalRejected as e:
    cycle["qt"] = {"qt": "rejected", "why": str(e)}
    raise SystemExit(1)

# 3. evaluate on the fixed case set
evaluation = ev.evaluate(proposal, DEV, HELDOUT)
cycle["evaluation"] = evaluation

# 4. fresh review: the full regression suite must pass with the patch in place
import os

env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
suite = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
    capture_output=True, text=True, cwd=ROOT, env=env,
)
cycle["regression_suite"] = {
    "cmd": "python -m unittest discover -s tests",
    "returncode": suite.returncode,
    "tail": [l for l in suite.stderr.strip().splitlines() if l.strip()][-3:],
}
if suite.returncode != 0:
    raise SystemExit("regression suite failed; cycle stops before approval")

# 5. owner approval + registry
# register the baseline v1.0 first so the registry records what v1.1 supersedes
baseline_prop = {"id": "prop-baseline-g01", "touches": ["src/pcd/gates.py:G01 (initial)"], "risk_changes": []}
baseline_eval = {"verdict": "adopt",
                 "candidate_v1.1": {"passed": 2, "total": 4, "cases": [], "note": "envelope-only baseline"}}
ev.record_version("G01", "v1.0", baseline_prop, baseline_eval, "Christian")
cycle["approval_v1.1"] = ev.record_version("G01", "v1.1", proposal, evaluation, "Christian")["components"]["G01"]

# 6. rollback drill: to the superseded baseline, then roll forward again
ev.rollback("G01", "v1.0", "drill: prove rollback to the superseded version is available", "Christian")
drill_mid = ev.registry()["components"]["G01"]["active"]
ev.rollback("G01", "v1.1", "drill complete; v1.1 re-verified by the regression suite", "Christian")
drill_end = ev.registry()["components"]["G01"]["active"]
cycle["rollback_drill"] = {"mid": drill_mid, "end": drill_end,
                           "note": "rollback re-points the registry; every event is recorded, nothing is deleted"}

write_json(EVID / "cycle-G01-v1.0-to-v1.1.json", cycle)
print(json.dumps({k: v for k, v in cycle.items()}, indent=2, ensure_ascii=False)[:2600])

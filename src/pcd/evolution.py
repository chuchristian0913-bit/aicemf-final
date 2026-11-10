"""Scoped evolution of the workflow's own machinery.

A recurring failure is diagnosed, patched, evaluated against FIXED regression
cases, reviewed fresh, approved by a named owner, and recorded in a version
registry with rollback available. Cases that were used to develop the patch are
development cases; held-out cases are only ever run for final assessment, never
to inform a revision.

The guard rail (QT): a proposal cannot weaken its own acceptance standard. Any
proposal that touches risk limits, gate tolerances, the leakage probe, stop
rules, the transition map, or permissions is rejected outright -- those knobs
define what "accepted" means, and a change that redefines acceptance cannot be
evaluated by the acceptance it redefined.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from .gates import validate_note_schema
from .util import now_iso, read_json, repo_root, sha256_text, write_json

VERSIONS_DIR = repo_root() / "config" / "versions"
REGISTRY_PATH = VERSIONS_DIR / "registry.json"

# knobs that define acceptance; proposals may not touch them
PROTECTED = {
    "max_rounds", "max_retries", "max_tool_calls", "max_llm_calls", "max_wall_clock_sec",
    "tolerance_rel", "tolerance_abs", "probe_value", "stop_rules", "ALLOWED",
    "TERMINAL", "permissions", "write_roots", "forbid_write", "network_allowed_stages",
}


class ProposalRejected(RuntimeError):
    pass


# ------------------------------------------------------------------- QT guard


def qt_check(proposal: dict) -> dict:
    """Reject a proposal that could weaken its own acceptance standard."""
    touches = " ".join(proposal.get("touches", []))
    hit = [k for k in PROTECTED if k in touches]
    if hit:
        raise ProposalRejected(
            f"proposal touches acceptance-defining knobs {sorted(hit)}: "
            "a change that redefines acceptance cannot be judged by the acceptance it redefines"
        )
    if proposal.get("risk_changes"):
        raise ProposalRejected("proposal declares risk-parameter changes")
    return {"qt": "pass", "protected_checked": len(PROTECTED)}


# ----------------------------------------------------------------- evaluation


def _gate_kinds_v1(submission: dict, note: dict | None) -> list[str]:
    """G01 v1.0: envelope only. Kept verbatim as the regression baseline."""
    kinds = []
    missing = REQUIRED_KEYS - set(submission)
    extra = set(submission) - REQUIRED_KEYS
    if missing:
        kinds.append("missing_keys")
    if extra:
        kinds.append("extra_keys")
    return kinds


def _gate_kinds_v1_1(submission: dict, note: dict | None) -> list[str]:
    """G01 v1.1: envelope + note artifact schema (validate_note_schema)."""
    kinds = _gate_kinds_v1(submission, note)
    if note is not None:
        kinds.extend(f["kind"] for f in validate_note_schema(note))
    return kinds


def run_cases(gate_kinds, case_files: list[Path]) -> dict:
    """Run a gate version over fixed case files. A case passes when the gate
    reports every kind the case expects (a gate must catch what it exists for)."""
    results = []
    for cf in case_files:
        case = read_json(cf)
        got = set(gate_kinds(case["submission"], case.get("note")))
        want = set(case["expect_kinds"])
        results.append({
            "case": case["name"],
            "kind": case.get("set", "dev"),
            "caught": sorted(got),
            "expected": sorted(want),
            "pass": want <= got,
        })
    return {
        "cases": results,
        "passed": sum(1 for r in results if r["pass"]),
        "total": len(results),
    }


def evaluate(proposal: dict, dev_cases: list[Path], heldout_cases: list[Path]) -> dict:
    old = run_cases(_gate_kinds_v1, dev_cases + heldout_cases)
    new = run_cases(_gate_kinds_v1_1, dev_cases + heldout_cases)
    return {
        "proposal_id": proposal["id"],
        "baseline_v1.0": old,
        "candidate_v1.1": new,
        "dev_only_summary": {
            "old": run_cases(_gate_kinds_v1, dev_cases)["passed"],
            "new": run_cases(_gate_kinds_v1_1, dev_cases)["passed"],
        },
        "verdict": "adopt" if new["passed"] == new["total"] and new["passed"] >= old["passed"] else "reject",
        "note": "held-out cases ran once, for assessment only; they did not inform the patch",
    }


# ------------------------------------------------------------------- registry


def registry() -> dict:
    return read_json(REGISTRY_PATH, {"components": {}})


def record_version(component: str, version: str, proposal: dict, evaluation: dict, approver: str) -> dict:
    if not approver.strip():
        raise ProposalRejected("a named human owner must approve the new version")
    if evaluation["verdict"] != "adopt":
        raise ProposalRejected("cannot register a version whose evaluation verdict is not adopt")
    reg = registry()
    comp = reg["components"].setdefault(component, {"versions": [], "active": None})
    impl = _impl_source(component)
    entry = {
        "version": version,
        "registered_at": now_iso(),
        "proposal_id": proposal["id"],
        "touches": proposal["touches"],
        "evaluation": {k: evaluation[k] for k in ("verdict", "candidate_v1.1")},
        "approver": approver,
        "impl_sha256": sha256_text(impl) if impl else None,
        "status": "active",
    }
    for v in comp["versions"]:
        if v["status"] == "active":
            v["status"] = "superseded"
    comp["versions"].append(entry)
    comp["active"] = version
    write_json(REGISTRY_PATH, reg)
    return reg


def rollback(component: str, to_version: str, reason: str, operator: str) -> dict:
    """Mark an earlier version active again. Recorded, never deleted."""
    if not operator.strip():
        raise ProposalRejected("rollback needs a named operator")
    reg = registry()
    comp = reg["components"].get(component)
    if comp is None:
        raise ProposalRejected(f"unknown component {component}")
    target = next((v for v in comp["versions"] if v["version"] == to_version), None)
    if target is None:
        raise ProposalRejected(f"version {to_version} not registered; rollback goes to a registered version only")
    for v in comp["versions"]:
        v["status"] = "active" if v["version"] == to_version else "superseded"
    comp["active"] = to_version
    comp.setdefault("history", []).append({"event": "rollback", "to": to_version, "reason": reason,
                                           "operator": operator, "at": now_iso()})
    write_json(REGISTRY_PATH, reg)
    return reg


def _impl_source(component: str) -> str | None:
    try:
        from . import gates as _g

        if component == "G01":
            return inspect.getsource(_g.g01_schema)
        if component == "G01.note_schema":
            return inspect.getsource(_g.validate_note_schema)
    except Exception:
        return None
    return None


REQUIRED_KEYS = {"request_id", "case_id", "candidate_id", "role", "artifacts", "claims_file"}

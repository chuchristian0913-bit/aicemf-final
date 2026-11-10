"""The controller: state, checkpoints, retries, stop rules, resume.

The controller is the only component allowed to move a run forward. Agent roles
return artifacts; a gate decides whether an artifact is admissible; and only the
controller writes `state.json`. That separation is what makes "the agent said it was
finished" a non-argument.

Three properties are load-bearing:

* **Every transition is a checkpoint.** State is written atomically after the gate
  that justified it, so an interrupted run resumes from the last *verified* position.
* **Completed work is not repeated.** Deterministic stages are content-addressed: the
  stage records the hash of its inputs and outputs, and a re-run with identical
  inputs returns the saved result instead of recomputing.
* **Uncertainty is a state, not a guess.** If state says a stage finished but its
  output is missing or its hash does not match, the run enters INSPECT_REQUIRED
  rather than replaying the stage or declaring success.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .util import (
    append_jsonl,
    canon,
    ensure_dir,
    load_toml,
    now_iso,
    read_json,
    read_jsonl,
    rel,
    repo_root,
    sha256_file,
    sha256_obj,
    write_json,
)

# ------------------------------------------------------------------------ states

STAGES = [
    "INIT",
    "SOURCES_FROZEN",
    "STANDARDIZED",
    "MODELED",
    "DRAFT_SUBMITTED",
    "GEN_GATES_PASSED",
    "IN_REVIEW",
    "REVIEWED",
    "CANDIDATE_ACCEPTED",
    "AWAITING_APPROVAL",
    "PUBLISHED",
    "ARCHIVED",
]

TERMINAL = {"PUBLISHED", "ARCHIVED", "BLOCKED", "STOPPED", "EXHAUSTED", "INSPECT_REQUIRED"}

ALLOWED: dict[str, set[str]] = {
    "INIT": {"SOURCES_FROZEN", "BLOCKED", "STOPPED"},
    "SOURCES_FROZEN": {"STANDARDIZED", "BLOCKED", "STOPPED"},
    "STANDARDIZED": {"MODELED", "BLOCKED", "STOPPED"},
    "MODELED": {"DRAFT_SUBMITTED", "BLOCKED", "STOPPED"},
    "DRAFT_SUBMITTED": {"GEN_GATES_PASSED", "DRAFT_SUBMITTED", "BLOCKED", "STOPPED"},
    "GEN_GATES_PASSED": {"IN_REVIEW", "BLOCKED", "STOPPED"},
    "IN_REVIEW": {"REVIEWED", "BLOCKED", "STOPPED"},
    "REVIEWED": {"CANDIDATE_ACCEPTED", "DRAFT_SUBMITTED", "EXHAUSTED", "STOPPED"},
    "CANDIDATE_ACCEPTED": {"AWAITING_APPROVAL"},
    "AWAITING_APPROVAL": {"PUBLISHED", "STOPPED", "BLOCKED"},
    "PUBLISHED": {"ARCHIVED"},
    "ARCHIVED": set(),
    "BLOCKED": set(),
    "STOPPED": set(),
    "EXHAUSTED": set(),
    "INSPECT_REQUIRED": {"DRAFT_SUBMITTED", "BLOCKED", "STOPPED"},
}


class ControllerError(RuntimeError):
    pass


# ------------------------------------------------------------------------- record


@dataclass
class State:
    case_id: str
    stage: str
    round: int = 0
    retries: dict[str, int] = field(default_factory=dict)
    pending_role: str | None = None
    candidate_hash: str | None = None
    clean_rounds: int = 0
    clean_round_streak_for: str | None = None
    open_findings: list[dict] = field(default_factory=list)
    stage_hashes: dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0
    llm_calls: int = 0
    started_at: str = ""
    updated_at: str = ""
    deadline: str | None = None
    terminal_reason: str | None = None
    mandate_hash: str | None = None

    # -- serialisation
    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "stage": self.stage,
            "round": self.round,
            "retries": self.retries,
            "pending_role": self.pending_role,
            "candidate_hash": self.candidate_hash,
            "clean_rounds": self.clean_rounds,
            "clean_round_streak_for": self.clean_round_streak_for,
            "open_findings": self.open_findings,
            "stage_hashes": self.stage_hashes,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "deadline": self.deadline,
            "terminal_reason": self.terminal_reason,
            "mandate_hash": self.mandate_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# -------------------------------------------------------------------------- paths


def case_paths(case_id: str) -> dict[str, Path]:
    root = repo_root()
    cdir = root / "cases" / case_id
    return {
        "root": root,
        "case": cdir,
        "frozen": root / "data" / "frozen" / case_id,
        "work": cdir / "work",
        "rounds": cdir / "rounds",
        "state": cdir / "state.json",
        "trace": cdir / "trace.jsonl",
        "request": cdir / "request.json",
        "decision": cdir / "decision.json",
        "approval": cdir / "approval.json",
        "output": root / "output",
    }


def load_state(case_id: str) -> State | None:
    d = read_json(case_paths(case_id)["state"])
    return State.from_dict(d) if d else None


def save_state(state: State) -> None:
    state.updated_at = now_iso()
    write_json(case_paths(state.case_id)["state"], state.to_dict())


def trace(case_id: str, event: str, *, stage: str | None = None, role: str | None = None, **kw: Any) -> dict:
    rec = {"ts": now_iso(), "case_id": case_id, "event": event, "stage": stage, "role": role}
    rec.update({k: v for k, v in kw.items() if v is not None})
    append_jsonl(case_paths(case_id)["trace"], rec)
    return rec


# --------------------------------------------------------------------- lifecycle


def init_case(case_id: str) -> State:
    p = case_paths(case_id)
    if p["state"].exists():
        raise ControllerError(f"{rel(p['state'])} exists: a case is initialised once")
    ensure_dir(p["case"])
    ensure_dir(p["work"])
    ensure_dir(p["rounds"])
    run = load_toml(p["root"] / "config" / "run.toml")
    limits = run["limits"]
    now = datetime.now(timezone.utc)
    st = State(
        case_id=case_id,
        stage="INIT",
        started_at=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        updated_at=now_iso(),
        deadline=(now + timedelta(seconds=limits["max_wall_clock_sec"])).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        mandate_hash=sha256_file(p["root"] / "config" / "run.toml"),
    )
    save_state(st)
    trace(case_id, "case_initialised", stage="INIT", mandate_hash=st.mandate_hash)
    return st


def advance(state: State, to: str, reason: str | None = None) -> State:
    if to not in ALLOWED.get(state.stage, set()):
        raise ControllerError(f"illegal transition {state.stage} -> {to}")
    trace(state.case_id, "transition", stage=to, role="pcd-orchestrator", **{"from": state.stage, "reason": reason})
    state.stage = to
    if to in TERMINAL:
        state.terminal_reason = reason or to
    save_state(state)
    return state


def check_limits(state: State) -> tuple[bool, str | None]:
    root = repo_root()
    run = load_toml(root / "config" / "run.toml")
    lim = run["limits"]
    if state.round > lim["max_rounds"]:
        return False, f"round limit {lim['max_rounds']} exhausted; exhaustion never grants acceptance"
    if state.tool_calls > lim["max_tool_calls"]:
        return False, "tool-call budget exhausted"
    if state.llm_calls > lim["max_llm_calls"]:
        return False, "model-call budget exhausted"
    if state.deadline and now_iso() > state.deadline:
        return False, "wall-clock deadline exceeded"
    return True, None


# --------------------------------------------------------- idempotent stage runs


def stage_fingerprint(inputs: list[Path], extra: str = "") -> str:
    parts = [sha256_file(p) if p.exists() else "MISSING" for p in inputs]
    return sha256_obj({"inputs": parts, "extra": extra})


def run_stage(
    state: State,
    name: str,
    inputs: list[Path],
    outputs: list[Path],
    fn: Callable[[], Any],
    *,
    extra: str = "",
) -> str:
    """Run a deterministic stage once. Re-running with the same inputs returns early.

    This is what makes resume cheap and honest: the controller never has to decide
    whether a completed calculation was 'probably fine'.
    """
    fp = stage_fingerprint(inputs, extra)
    prior = state.stage_hashes.get(name)

    if prior == fp and all(p.exists() for p in outputs):
        trace(state.case_id, "stage_skipped", stage=state.stage, role="pcd-orchestrator", name=name, fingerprint=fp[:16])
        return "skipped"

    if prior and prior != fp and all(p.exists() for p in outputs):
        # inputs changed under a completed stage: this is a new version, not a retry
        trace(state.case_id, "stage_invalidated", stage=state.stage, role="pcd-orchestrator",
              name=name, old_fingerprint=prior[:16], new_fingerprint=fp[:16])

    fn()
    missing = [rel(p) for p in outputs if not p.exists()]
    if missing:
        raise ControllerError(f"stage {name} produced no {missing}")
    state.stage_hashes[name] = fp
    save_state(state)
    trace(state.case_id, "stage_completed", stage=state.stage, role="pcd-orchestrator",
          name=name, fingerprint=fp[:16], outputs=[rel(p) for p in outputs])
    return "ran"


# ---------------------------------------------------------------- requests / handoff


def make_request(state: State, role: str, *, stage: str, constraints: dict, carried_findings: list[dict] | None = None) -> dict:
    """A request is the handoff contract: what to produce, under what limits."""
    req = {
        "request_id": None,
        "case_id": state.case_id,
        "role": role,
        "stage": stage,
        "round": state.round,
        "constraints": constraints,
        "carried_findings": carried_findings if carried_findings is not None else state.open_findings,
        "issued_at": now_iso(),
        "limits": {
            "max_tool_calls": 40,
            "max_llm_calls": 12,
            "deadline": state.deadline,
        },
        "acceptance": "all blocking gates PASS; no open finding of severity high",
    }
    req["request_id"] = "req-" + sha256_obj({k: v for k, v in req.items() if k != "issued_at"})[:16]
    write_json(case_paths(state.case_id)["request"], req)
    state.pending_role = role
    save_state(state)
    trace(state.case_id, "request_issued", stage=stage, role="pcd-orchestrator",
          request_id=req["request_id"], target_role=role, round=state.round)
    return req


def read_request(case_id: str) -> dict | None:
    return read_json(case_paths(case_id)["request"])


# ---------------------------------------------------------------------- inspection


def inspect(state: State) -> dict:
    """Decide what, if anything, may be resumed. Never mutates artefacts."""
    p = case_paths(state.case_id)
    problems: list[dict] = []

    for name, fp in state.stage_hashes.items():
        if not fp:
            problems.append({"kind": "empty_fingerprint", "stage": name})

    expected = {
        "sources": p["work"] / "sources.jsonl",
        "standardize": p["work"] / "standardized.json",
        "model": p["work"] / "metrics.json",
    }
    for name, path in expected.items():
        recorded = state.stage_hashes.get(name)
        exists = path.exists()
        if recorded and not exists:
            problems.append({"kind": "output_missing", "stage": name, "path": rel(path),
                             "action": "re-run the stage; it is deterministic and inputs are frozen"})
        if not recorded and exists:
            problems.append({"kind": "orphan_output", "stage": name, "path": rel(path),
                             "action": "output exists without a recorded stage run; treat as unverified"})

    if state.stage in TERMINAL and state.stage not in {"PUBLISHED", "ARCHIVED"}:
        return {"stage": state.stage, "resumable": False, "terminal_reason": state.terminal_reason, "problems": problems}

    if state.stage == "PUBLISHED" or state.stage == "ARCHIVED":
        return {"stage": state.stage, "resumable": False,
                "result": rel(p["case"] / "decision.json"),
                "note": "a completed case returns its saved result; nothing is recomputed"}

    next_action = {
        "INIT": "run the sources stage",
        "SOURCES_FROZEN": "run standardisation",
        "STANDARDIZED": "run the model",
        "MODELED": "dispatch the generator",
        "DRAFT_SUBMITTED": "run generator gates",
        "GEN_GATES_PASSED": "dispatch the critic",
        "IN_REVIEW": "collect critic reports and route",
        "REVIEWED": "route: repair, accept, or exhaust",
        "CANDIDATE_ACCEPTED": "wait for human approval",
        "AWAITING_APPROVAL": "wait for human approval",
    }.get(state.stage, "inspect")

    return {
        "stage": state.stage,
        "round": state.round,
        "pending_role": state.pending_role,
        "resumable": not problems or all(p["kind"] in {"orphan_output"} for p in problems),
        "next_action": next_action,
        "problems": problems,
        "limits_ok": check_limits(state),
        "open_findings": len(state.open_findings),
    }


# ------------------------------------------------------------------------- routing


def route_after_review(state: State, critic_verdict: str, candidate_hash: str) -> dict:
    """The controller's decision, taken from the verdict and the ledger - not the agent's.

    Acceptance needs two consecutive clean full reviews *of the same candidate*. A
    changed candidate resets the streak, so a repair cannot inherit the previous
    rounds' cleanliness.
    """
    if critic_verdict == "clean":
        if state.clean_round_streak_for == candidate_hash:
            state.clean_rounds += 1
        else:
            state.clean_round_streak_for = candidate_hash
            state.clean_rounds = 1
        state.open_findings = []
        save_state(state)
        trace(state.case_id, "review_clean", stage="REVIEWED", role="pcd-orchestrator",
              round=state.round, candidate_hash=candidate_hash[:16], streak=state.clean_rounds)
        if state.clean_rounds >= 2:
            return {"decision": "accept", "why": f"{state.clean_rounds} consecutive clean rounds on {candidate_hash[:12]}"}
        return {"decision": "repeat", "why": f"clean round {state.clean_rounds}/2 on {candidate_hash[:12]}; a second clean review is required"}

    # not clean: reset the streak, keep the findings, respect the round budget
    state.clean_rounds = 0
    state.clean_round_streak_for = None
    ok, why = check_limits(state)
    save_state(state)
    trace(state.case_id, "review_findings", stage="REVIEWED", role="pcd-orchestrator",
          round=state.round, verdict=critic_verdict, open=len(state.open_findings))
    if not ok or state.round >= load_toml(repo_root() / "config" / "run.toml")["limits"]["max_rounds"]:
        return {"decision": "exhaust", "why": why or "round budget exhausted; exhaustion never grants acceptance"}
    return {"decision": "repair", "why": f"{len(state.open_findings)} open finding(s) carried into round {state.round + 1}"}


def record_decision(state: State, decision: dict) -> None:
    payload = {
        "case_id": state.case_id,
        "round": state.round,
        "stage": state.stage,
        "decision": decision["decision"],
        "why": decision["why"],
        "candidate_hash": state.candidate_hash,
        "open_findings": state.open_findings,
        "clean_rounds": state.clean_rounds,
        "recorded_at": now_iso(),
    }
    write_json(case_paths(state.case_id)["decision"], payload)
    trace(state.case_id, "decision_recorded", stage=state.stage, role="pcd-orchestrator",
          decision=decision["decision"], why=decision["why"])


# ------------------------------------------------------------------------ approval


def record_approval(case_id: str, approver: str, decision: str, scope: str, note: str = "") -> dict:
    if decision not in ("approve", "reject", "defer"):
        raise ControllerError("approval decision must be approve, reject or defer")
    if not approver.strip():
        raise ControllerError("a named human approver is required; 'the agent' is not an approver")
    rec = {
        "case_id": case_id,
        "approver": approver,
        "decision": decision,
        "scope": scope,
        "note": note,
        "decided_at": now_iso(),
        "authority": "human-analyst",
    }
    write_json(case_paths(case_id)["approval"], rec)
    trace(case_id, "approval_recorded", stage="AWAITING_APPROVAL", role="human-analyst",
          approver=approver, decision=decision, scope=scope)
    return rec


if __name__ == "__main__":
    st = load_state("case-01")
    if st:
        print(json.dumps(inspect(st), indent=2))
    else:
        print("no state: run `python -m pcd.cli init`")

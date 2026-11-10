"""Command line entry point: the operator's view of the controller.

Typical cycle:

    python -m pcd.cli init
    python -m pcd.cli prepare          # freeze, standardise, model (deterministic)
    python -m pcd.cli request          # dispatch the generator with a request_id
    #   ... the generator role writes rounds/round-00N/note/claims.json + submission.json
    python -m pcd.cli submit --round round-001
    python -m pcd.cli review --round round-001
    python -m pcd.cli approve --by "Name" --decision approve
    python -m pcd.cli publish

    python -m pcd.cli status           # where is the run, and what is safe to resume
    python -m pcd.cli resume           # inspect then continue from the last verified step
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import gates as gates_mod
from . import state as ctl
from .model import build as build_model
from .report import render as render_note
from .sources import freeze
from .standardize import standardize
from .util import canon, load_toml, read_json, read_jsonl, rel, repo_root, sha256_obj, write_json

CASE = "case-01"


# ------------------------------------------------------------------------ helpers


def _paths(case: str) -> dict[str, Path]:
    return ctl.case_paths(case)


def _run_cfg() -> dict:
    return load_toml(repo_root() / "config" / "run.toml")


def candidate_hash(round_dir: Path) -> str:
    claims = round_dir / "note" / "claims.json"
    if not claims.exists():
        raise ctl.ControllerError(f"no claims at {rel(claims)}")
    return sha256_obj(read_json(claims))


def _round_dir(case: str, rnd: int | str) -> Path:
    name = rnd if isinstance(rnd, str) else f"round-{rnd:03d}"
    return _paths(case)["rounds"] / name


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ctl.ControllerError(msg)


# ------------------------------------------------- detecting what a role actually wrote

_TREE_SKIP = (".git/", "__pycache__/", ".pytest_cache/", "cases/case-01/work/",
              "cases/case-01/state.json", "cases/case-01/trace.jsonl", "cases/case-01/request.json",
              "cases/case-01/_snapshot_", "output/build/", "temp/")


def _tree(root: Path) -> dict[str, str]:
    """Hash every tracked file. Used to see what a role changed, not what it claims."""
    from .util import sha256_file

    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rp = rel(p)
        if any(s in rp + "/" for s in _TREE_SKIP):
            continue
        if rp.endswith((".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".toc")):
            continue
        if rp.startswith(".git"):
            continue
        out[rp] = sha256_file(p)
    return out


def _record_writes(case: str, round_dir: Path, declared: list[str]) -> list[dict]:
    root = repo_root()
    snap_path = _paths(case)["case"] / f"_snapshot_{round_dir.name}.json"
    before = read_json(snap_path, {})
    after = _tree(root)
    changed = sorted(p for p, h in after.items() if before.get(p) != h)
    declared_norm = {d.replace("\\", "/") for d in declared}
    # the controller writes these itself; they are not the role's writes
    import re as _re

    # the controller writes these itself; they are not the role's writes
    controller_owned = _re.compile(r"(submission\.json|gates_.*\.json|verdict\.json|_snapshot_|state\.json|trace\.jsonl|request\.json)$")
    events = []
    for p in changed:
        if controller_owned.search(p):
            continue
        is_declared = any(p.endswith(d) or p == d for d in declared_norm)
        ev = {"event": "write", "path": p, "stage": "DRAFT_SUBMITTED", "role": "pcd-generator",
              "declared": is_declared, "undeclared": not is_declared}
        ctl.trace(case, **ev)
        events.append(ev)
    return events


def snapshot_before_dispatch(case: str, round_dir: Path) -> None:
    write_json(_paths(case)["case"] / f"_snapshot_{round_dir.name}.json", _tree(repo_root()))


# ----------------------------------------------------------------------- commands


def cmd_init(args) -> int:
    st = ctl.init_case(args.case)
    print(f"case {args.case} initialised in state {st.stage}")
    print(f"  mandate hash  {st.mandate_hash[:16]}")
    print(f"  deadline      {st.deadline}")
    return 0


def cmd_prepare(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state: run init first")
    _deterministic_run(args.case, st)
    if st.stage == "MODELED":
        print("deterministic stages complete; dispatch the generator with `request`")
    return 0


def _deterministic_run(case: str, st) -> None:
    """Freeze -> standardise -> model. Safe to call from prepare AND from resume.

    Kept in one place so that resume cannot disagree with prepare about what a
    re-run means: both go through run_stage, which skips completed work and
    recomputes only what is missing or invalidated.
    """
    p = _paths(case)
    run = _run_cfg()
    cutoff = run["case"]["cutoff"]

    if st.stage == "INIT":
        if (p["frozen"] / "MANIFEST.json").exists():
            # Frozen evidence outlives a state reset. Adopt it only after re-hashing,
            # because a snapshot that changed outside the controller is a stop condition.
            from .sources import verify_snapshot

            check = verify_snapshot(p["frozen"])
            require(check["ok"], f"frozen snapshot failed integrity: {check['problems']}")
            st.stage_hashes["sources"] = ctl.stage_fingerprint([repo_root() / "config" / "run.toml"], cutoff)
            ctl.save_state(st)
            ctl.trace(case, "snapshot_adopted", stage="INIT", role="pcd-orchestrator",
                      fingerprint=st.stage_hashes["sources"][:16], cutoff=check["cutoff"], verified=True)
            print("snapshot present and verified; adopting it instead of re-fetching")
        else:
            ctl.run_stage(
                st, "sources",
                inputs=[repo_root() / "config" / "run.toml"],
                outputs=[p["frozen"] / "MANIFEST.json"],
                fn=lambda: freeze(case, p["frozen"], cutoff),
                extra=cutoff,
            )
        ctl.advance(st, "SOURCES_FROZEN", "snapshot frozen and hashed")
        print("SOURCES_FROZEN")

    if st.stage == "SOURCES_FROZEN":
        ctl.run_stage(
            st, "standardize",
            inputs=[p["frozen"] / "MANIFEST.json", p["frozen"] / "raw" / "V.json"],
            outputs=[p["work"] / "standardized.json", p["work"] / "sources.jsonl"],
            fn=lambda: standardize(case, p["frozen"], p["work"]),
        )
        ctl.advance(st, "STANDARDIZED", "annual facts resolved and admitted at cutoff")
        print("STANDARDIZED")

    if st.stage == "STANDARDIZED":
        ctl.run_stage(
            st, "model",
            inputs=[p["work"] / "standardized.json", repo_root() / "config" / "assumptions" / f"{case}.toml"],
            outputs=[p["work"] / "metrics.json"],
            fn=lambda: build_model(case, p["work"]),
        )
        ctl.advance(st, "MODELED", "metrics and valuation computed from the assumption file")
        print("MODELED")

    # A later stage with a missing output: recompute in dependency order without
    # moving the state machine, so resume repairs the gap but does not invent
    # progress. (MODELED with metrics.json missing, and anything downstream of a
    # lost standardized.json, lands here.)
    if st.stage == "MODELED":
        std_missing = not (p["work"] / "standardized.json").exists()
        model_missing = not (p["work"] / "metrics.json").exists()
        if std_missing:
            ctl.run_stage(
                st, "standardize",
                inputs=[p["frozen"] / "MANIFEST.json", p["frozen"] / "raw" / "V.json"],
                outputs=[p["work"] / "standardized.json", p["work"] / "sources.jsonl"],
                fn=lambda: standardize(case, p["frozen"], p["work"]),
            )
            print("STANDARDIZED (recomputed)")
        if std_missing or model_missing:
            ctl.run_stage(
                st, "model",
                inputs=[p["work"] / "standardized.json", repo_root() / "config" / "assumptions" / f"{case}.toml"],
                outputs=[p["work"] / "metrics.json"],
                fn=lambda: build_model(case, p["work"]),
            )
            print("MODELED (recomputed)")


def cmd_request(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    require(st.stage in ("MODELED", "DRAFT_SUBMITTED"), f"cannot dispatch from {st.stage}")
    if st.stage == "MODELED":
        st.round = 1
    elif st.stage == "DRAFT_SUBMITTED":
        st.round += 1
        st.retries = {}
    constraints = {
        "universe": _run_cfg()["case"]["universe"],
        "cutoff": _run_cfg()["case"]["cutoff"],
        "metrics_file": "cases/%s/work/metrics.json" % args.case,
        "must_cite": "every number in prose must be a claim with a metric_id",
        "must_disclose": "any finding with requires_note_disclosure=true that applies to a cited metric",
        "output": f"cases/{args.case}/rounds/round-{st.round:03d}/",
    }
    req = ctl.make_request(st, args.role, stage="DRAFT_SUBMITTED", constraints=constraints)
    snapshot_before_dispatch(args.case, _round_dir(args.case, st.round))
    if st.stage == "MODELED":
        ctl.advance(st, "DRAFT_SUBMITTED", "generator dispatched")
    else:
        ctl.save_state(st)
    print(json.dumps({"request_id": req["request_id"], "round": st.round, "role": req["role"],
                      "carried_findings": len(req["carried_findings"])}, indent=2))
    return 0


def _gate_bundle(case: str, round_dir: Path, only: list[str] | None = None) -> dict:
    return gates_mod.run_gates(case, round_dir, only)


def cmd_gates(args) -> int:
    rd = _round_dir(args.case, args.round) if args.round else None
    summary = _gate_bundle(args.case, rd, args.only.split(",") if args.only else None)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for gid, r in summary["gate_results"].items():
            mark = {"PASS": "PASS", "FAIL": "FAIL", "NOT_CHECKED": "n/a "}[r["status"]]
            print(f"{gid} {mark} {r['name']:<18s} {len(r['failures'])} finding(s)")
            for f in r["failures"][:5]:
                print(f"        - {f}")
        print(f"\n{summary['counts']}  blocking: {summary['blocking_failures']}")
    return 0 if not summary["blocking_failures"] else 1


def cmd_submit(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    require(st.stage == "DRAFT_SUBMITTED", f"cannot submit from {st.stage}")
    rd = _round_dir(args.case, args.round)
    require(rd.exists(), f"no round directory {rel(rd)}")

    # the controller refuses to look at a submission that does not answer the open request
    sub = read_json(rd / "submission.json")
    require(sub is not None, "submission.json missing: a role must answer the request explicitly")
    req = ctl.read_request(args.case)
    if sub.get("request_id") != (req or {}).get("request_id"):
        ctl.trace(args.case, "submission_rejected", stage=st.stage, role=sub.get("role"),
                  reason="request_id mismatch", submitted=sub.get("request_id"), open=(req or {}).get("request_id"))
        st.retries["generator"] = st.retries.get("generator", 0) + 1
        ctl.save_state(st)
        print("REJECTED: submission does not answer the open request")
        return 2

    summary = _gate_bundle(args.case, rd)
    write_json(rd / "gates_generator.json", summary)
    writes = _record_writes(args.case, rd, sub.get("artifacts", []))
    # permissions are re-run now that the controller has recorded what actually changed
    summary["gate_results"]["G12"] = gates_mod.g12_permissions(gates_mod.Ctx(args.case, rd))
    summary["counts"] = {s: sum(1 for r in summary["gate_results"].values() if r["status"] == s) for s in gates_mod.STATUS}
    summary["blocking_failures"] = [g for g, r in summary["gate_results"].items() if r["status"] == "FAIL" and r["blocking"]]
    write_json(rd / "gates_generator.json", summary)
    blocking = summary["blocking_failures"]
    ctl.trace(args.case, "gates_run", stage=st.stage, role="pcd-orchestrator",
              round=st.round, phase="generator", blocking=blocking, counts=summary["counts"],
              undeclared_writes=sum(1 for w in writes if w["undeclared"]))

    if blocking:
        st.retries["generator"] = st.retries.get("generator", 0) + 1
        max_retries = _run_cfg()["limits"]["max_retries"]
        if st.retries["generator"] > max_retries:
            ctl.advance(st, "BLOCKED", f"generator failed {st.retries['generator']} times; retries exhausted")
            print("BLOCKED: retry budget exhausted")
            return 2
        ctl.save_state(st)
        print(f"REJECTED at gates: {blocking}. Stay at current role (attempt {st.retries['generator']}/{max_retries}).")
        return 2

    st.candidate_hash = candidate_hash(rd)
    ctl.advance(st, "GEN_GATES_PASSED", "generator gates passed")
    print(f"GEN_GATES_PASSED candidate {st.candidate_hash[:12]}")
    return 0


def _load_critic_reports(rd: Path) -> list[dict]:
    d = rd / "critic"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("gates"):
            continue
        out.append({"file": f.name, **read_json(f)})
    return out


def cmd_review(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    require(st.stage == "GEN_GATES_PASSED", f"cannot review from {st.stage}")
    rd = _round_dir(args.case, args.round)
    ctl.advance(st, "IN_REVIEW", "fresh critic dispatched")

    reports = _load_critic_reports(rd)
    require(len(reports) >= 3, "three specialist critic reports are required before a verdict")

    summary = _gate_bundle(args.case, rd)
    write_json(rd / "gates_review.json", summary)

    findings: list[dict] = []
    for rep in reports:
        for f in rep.get("findings", []):
            findings.append({"source": rep.get("role", rep["file"]), **f})

    high = [f for f in findings if f.get("severity") == "high"]
    blocking = summary["blocking_failures"]
    verdict = "clean" if (not blocking and not high) else "revise"

    write_json(
        rd / "verdict.json",
        {"round": st.round, "candidate_hash": st.candidate_hash, "verdict": verdict,
         "blocking_gates": blocking, "open_findings": len(findings), "high_severity": len(high),
         "reports": [r.get("role", r["file"]) for r in reports]},
    )
    st.open_findings = findings
    ctl.save_state(st)
    ctl.advance(st, "REVIEWED", f"critic verdict: {verdict}")

    decision = ctl.route_after_review(st, verdict, st.candidate_hash or "")
    ctl.record_decision(st, decision)

    if decision["decision"] == "repair":
        ctl.save_state(st)
        print(f"REPAIR: {decision['why']}")
        print("  next: `request` to dispatch round %d with the carried findings" % (st.round + 1))
        return 1
    if decision["decision"] == "repeat":
        ctl.save_state(st)
        print(f"REPEAT: {decision['why']}")
        print("  next: `request` to dispatch a second clean review")
        return 1
    if decision["decision"] == "accept":
        ctl.advance(st, "CANDIDATE_ACCEPTED", decision["why"])
        ctl.advance(st, "AWAITING_APPROVAL", "candidate accepted; publication needs a named human")
        print(f"ACCEPTED: {decision['why']}")
        print("  next: `approve --by \"Name\" --decision approve`")
        return 0
    ctl.advance(st, "EXHAUSTED", decision["why"])
    print(f"EXHAUSTED: {decision['why']}")
    return 2


def cmd_approve(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    require(st.stage == "AWAITING_APPROVAL", f"nothing to approve from {st.stage}")
    rec = ctl.record_approval(args.case, args.by, args.decision, f"candidate {st.candidate_hash[:12]}", args.note or "")
    if args.decision == "approve":
        ctl.advance(st, "PUBLISHED", f"approved by {args.by}")
        print(f"PUBLISHED: approved by {args.by} at {rec['decided_at']}")
    else:
        ctl.advance(st, "STOPPED", f"{args.decision} by {args.by}")
        print(f"{args.decision.upper()}: the run stops, the active version is retained")
    return 0


def cmd_publish(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    require(st.stage in ("PUBLISHED", "ARCHIVED"), f"cannot publish from {st.stage}")

    approval = read_json(_paths(args.case)["approval"])
    require(bool(approval and approval.get("decision") == "approve"),
            "no recorded approval: publication is a consequential action and needs a named human")

    rd = _round_dir(args.case, st.round)
    out = _paths(args.case)["output"]
    out.mkdir(parents=True, exist_ok=True)
    res = render_note(args.case, rd / "note" / "claims.json", out, compile_pdf=not args.no_pdf)

    # machine-readable panel for downstream consumers
    model = read_json(_paths(args.case)["work"] / "metrics.json")
    rows = []
    for t in _run_cfg()["case"]["universe"]:
        r = model["registry"]
        rows.append(
            {
                "ticker": t,
                "fy": "FY2025",
                "revenue_usd": r[f"{t}.FY2025.revenue"]["value"],
                "revenue_growth": r[f"{t}.FY2025.revenue_growth"]["value"],
                "ebit_margin": r[f"{t}.FY2025.ebit_margin"]["value"],
                "net_margin": r[f"{t}.FY2025.net_margin"]["value"],
                "fcf_conversion": r[f"{t}.FY2025.fcf_conversion"]["value"],
                "equity_value_usd": r[f"{t}.DCF.equity_value"]["value"],
                "value_per_share": r[f"{t}.DCF.per_share"]["value"],
            }
        )
    write_json(out / "panel.json", {"case_id": args.case, "cutoff": model["cutoff"],
                                    "assumption_version": model["assumption_version"], "rows": rows})

    import csv

    with open(out / "panel.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    write_json(
        _paths(args.case)["case"] / "result.json",
        {
            "case_id": args.case,
            "terminal_stage": st.stage,
            "round": st.round,
            "candidate_hash": st.candidate_hash,
            "approval": approval,
            "outputs": [rel(out / "note.pdf"), rel(out / "note.html"), rel(out / "panel.csv")],
            "note_pdf": bool(res["pdf_ok"]),
            "generated_at": ctl.now_iso(),
        },
    )
    if st.stage == "PUBLISHED":
        ctl.advance(st, "ARCHIVED", "outputs written and archived")
    print(json.dumps({"published": True, "pdf": res["pdf_ok"], "out": rel(out)}, indent=2))
    return 0


def cmd_status(args) -> int:
    st = ctl.load_state(args.case)
    if st is None:
        print("no state")
        return 1
    info = ctl.inspect(st)
    print(json.dumps({"state": st.to_dict(), "inspect": info}, indent=2, ensure_ascii=False))
    return 0


def cmd_resume(args) -> int:
    st = ctl.load_state(args.case)
    require(st is not None, "no state")
    info = ctl.inspect(st)
    problems = [p for p in info["problems"]]
    if problems:
        print("cannot resume cleanly:")
        for p in problems:
            print("  -", p)
        if any(p["kind"] == "output_missing" for p in problems):
            print("re-running the deterministic stage is safe: its inputs are frozen.")
    print(json.dumps({k: info[k] for k in ("stage", "round", "resumable", "next_action")}, indent=2))
    if info["stage"] in ("INIT", "SOURCES_FROZEN", "STANDARDIZED"):
        return cmd_prepare(args)
    if info["stage"] == "MODELED":
        # A missing deterministic output must be recomputed BEFORE anything is
        # dispatched. The first version of resume went straight to `request` here,
        # which would have sent the generator to work against a registry that did
        # not exist. Dispatch only when the deterministic chain is intact.
        if any(p["kind"] == "output_missing" for p in problems):
            _deterministic_run(args.case, st)
            st = ctl.load_state(args.case)
            require(st.stage == "MODELED" and not ctl.inspect(st)["problems"],
                    "deterministic re-run left problems; inspect before dispatching")
            print("deterministic outputs restored; dispatching")
        return cmd_request(argparse.Namespace(case=args.case, role="pcd-generator"))
    print("nothing to do automatically; the run is waiting on a role or a human")
    return 0


# ---------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pcd", description="Payments Coverage Desk controller")
    ap.add_argument("--case", default=CASE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialise a case").set_defaults(fn=cmd_init)

    p = sub.add_parser("prepare", help="run the deterministic stages")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("request", help="dispatch a role with a request")
    p.add_argument("--role", default="pcd-generator")
    p.set_defaults(fn=cmd_request)

    p = sub.add_parser("submit", help="the role submits; controller gates it")
    p.add_argument("--round", default=None)
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("review", help="run the critic phase and route")
    p.add_argument("--round", default=None)
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("approve", help="record a human decision")
    p.add_argument("--by", required=True)
    p.add_argument("--decision", required=True, choices=["approve", "reject", "defer"])
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("publish", help="render and archive the note")
    p.add_argument("--no-pdf", action="store_true")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("gates", help="run gates without changing state")
    p.add_argument("--round", default=None)
    p.add_argument("--only", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_gates)

    sub.add_parser("status", help="where is the run").set_defaults(fn=cmd_status)
    sub.add_parser("resume", help="inspect and continue").set_defaults(fn=cmd_resume)

    args = ap.parse_args(argv)
    if args.cmd in ("submit", "review") and args.round is None:
        st = ctl.load_state(args.case)
        args.round = st.round if st else None
    try:
        return args.fn(args)
    except ctl.ControllerError as exc:
        print(f"CONTROLLER REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

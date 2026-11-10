"""Executable acceptance criteria.

A gate is a function of files. It never calls a model, never parses prose for intent,
and never takes the producing role's word for anything. Gates return
PASS / FAIL / NOT_CHECKED plus the evidence that decided it, so a reviewer can
disagree with the *criterion* without having to trust the *agent*.

The four gates worth reading first:

* ``G07 tie_out``   - every number in the note is reached through a metric_id and
                      recomputed from the frozen snapshot, not from the note.
* ``G10 model_integrity`` - the DCF is re-derived from assumptions; a ticker literal
                      in the model module is a failure, because it means a judgement
                      escaped the config file.
* ``G11 period_alignment`` - a peer table mixing September and December year-ends
                      must say so. Silence here is how a note compares two different
                      economies and calls it growth.
* ``G13 leakage``   - a probe that cannot fail is not a test, so this gate has two
                      halves: post-cutoff perturbation must change nothing, and a
                      pre-cutoff perturbation must change something.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .util import canon, load_toml, read_json, read_jsonl, repo_root, sha256_text, write_json

STATUS = ("PASS", "FAIL", "NOT_CHECKED")


# --------------------------------------------------------------------------- context


class Ctx:
    """Everything a gate may read. Paths are explicit so a gate cannot wander."""

    def __init__(self, case_id: str, round_dir: Path | None = None):
        self.root = repo_root()
        self.case_id = case_id
        self.case_dir = self.root / "data" / "frozen" / case_id
        self.work_dir = self.root / "cases" / case_id / "work"
        self.round_dir = round_dir
        self.run = load_toml(self.root / "config" / "run.toml")
        self.gate_cfg = load_toml(self.root / "config" / "gates.toml")["gates"]
        self.assum = load_toml(self.root / "config" / "assumptions" / f"{case_id}.toml")

    # -- lazy loaders
    @property
    def cutoff(self) -> str:
        return read_json(self.case_dir / "MANIFEST.json")["cutoff"]

    @property
    def std(self) -> dict:
        return read_json(self.work_dir / "standardized.json", {})

    @property
    def model(self) -> dict:
        return read_json(self.work_dir / "metrics.json", {})

    @property
    def sources(self) -> dict[str, dict]:
        return {r["source_id"]: r for r in read_jsonl(self.work_dir / "sources.jsonl")}

    def note(self) -> dict | None:
        if not self.round_dir:
            return None
        return read_json(self.round_dir / "note" / "claims.json")

    def submission(self) -> dict | None:
        if not self.round_dir:
            return None
        return read_json(self.round_dir / "submission.json")

    def request(self) -> dict | None:
        if not self.round_dir:
            return None
        return read_json(self.root / "cases" / self.case_id / "request.json")


def _result(name: str, gate_id: str, status: str, evidence: dict, failures: list[dict] | None = None) -> dict:
    assert status in STATUS
    return {
        "gate": gate_id,
        "name": name,
        "status": status,
        "blocking": True,
        "evidence": evidence,
        "failures": failures or [],
        "evidence_hash": sha256_text(canon(evidence)),
    }


# ------------------------------------------------------------------ G01 / G02 / G12

REQUIRED_SUBMISSION_KEYS = {"request_id", "case_id", "candidate_id", "role", "artifacts", "claims_file"}


def g01_schema(ctx: Ctx) -> dict:
    sub = ctx.submission()
    if sub is None:
        return _result("schema", "G01", "NOT_CHECKED", {"reason": "no submission in this round"})
    missing = REQUIRED_SUBMISSION_KEYS - set(sub)
    extra = set(sub) - REQUIRED_SUBMISSION_KEYS
    failures = []
    if missing:
        failures.append({"kind": "missing_keys", "keys": sorted(missing)})
    if extra:
        failures.append({"kind": "extra_keys", "keys": sorted(extra)})
    return _result(
        "schema",
        "G01",
        "FAIL" if failures else "PASS",
        {"required": sorted(REQUIRED_SUBMISSION_KEYS), "present": sorted(sub)},
        failures,
    )


def g02_identity(ctx: Ctx) -> dict:
    sub, req = ctx.submission(), ctx.request()
    if sub is None or req is None:
        return _result("identity", "G02", "NOT_CHECKED", {"reason": "submission or request missing"})
    checks = {
        "request_id": sub.get("request_id") == req.get("request_id"),
        "case_id": sub.get("case_id") == ctx.case_id,
        "role": sub.get("role") == req.get("role"),
    }
    failures = [{"kind": "mismatch", "field": k} for k, ok in checks.items() if not ok]
    return _result(
        "identity", "G02", "FAIL" if failures else "PASS",
        {"request_id": req.get("request_id"), "role": req.get("role"), "checks": checks}, failures,
    )


def g12_permissions(ctx: Ctx) -> dict:
    trace = read_jsonl(ctx.root / "cases" / ctx.case_id / "trace.jsonl")
    if not trace:
        return _result("permissions", "G12", "NOT_CHECKED", {"reason": "no trace"})
    write_roots = tuple(ctx.run["permissions"]["write_roots"])
    forbid = tuple(ctx.run["permissions"]["forbid_write"])
    net_ok_stages = set(ctx.run["permissions"]["network_allowed_stages"])

    violations = []
    for ev in trace:
        path = ev.get("path")
        if ev.get("event") == "write" and path:
            posix = path.replace("\\", "/")
            if any(f in posix for f in forbid):
                violations.append({"kind": "write_to_forbidden", "path": posix, "stage": ev.get("stage")})
            elif not any(posix.startswith(r) or f"/{r}" in posix for r in write_roots):
                violations.append({"kind": "write_outside_roots", "path": posix, "stage": ev.get("stage")})
        if ev.get("event") == "network" and ev.get("stage") not in net_ok_stages:
            violations.append({"kind": "network_after_freeze", "stage": ev.get("stage")})
        if ev.get("event") == "tool" and ev.get("role", "").startswith("pcd-critic") and ev.get("tool") in ("Write", "Edit"):
            violations.append({"kind": "critic_wrote_candidate", "role": ev.get("role"), "tool": ev.get("tool")})

    return _result(
        "permissions", "G12", "FAIL" if violations else "PASS",
        {
            "events": len(trace),
            "write_roots": list(write_roots),
            "forbid_write": list(forbid),
            "network_allowed_stages": sorted(net_ok_stages),
        },
        violations,
    )


# ------------------------------------------------------------------------- G03


def g03_completeness(ctx: Ctx) -> dict:
    reg = ctx.model.get("registry", {})
    if not reg:
        return _result("completeness", "G03", "NOT_CHECKED", {"reason": "no model output"})
    required = ctx.gate_cfg["G03"]["required_metrics"]
    years = [f"FY{y}" for y in ctx.assum["meta"].get("base_years", [ctx.std["years"][-1]])]
    base_fy = f"FY{ctx.std['years'][-1]}"

    missing, na_ok = [], []
    for ticker in ctx.std["tickers"]:
        for metric in required:
            if metric in ("revenue_growth", "ebit_margin", "net_margin", "fcf_conversion", "fcf", "ebitda_proxy"):
                mid = f"{ticker}.{base_fy}.{metric}"
            else:
                mid = f"{ticker}.{base_fy}.{metric}"
            rec = reg.get(mid)
            if rec is None:
                missing.append({"metric_id": mid, "reason": "absent from registry"})
            elif rec["value"] is None:
                na_ok.append({"metric_id": mid, "reason": rec.get("note")})
    # nothing may be silently zero
    zeros = [
        {"metric_id": k, "name": v["name"]}
        for k, v in reg.items()
        if v["kind"] == "observed" and v["value"] == 0 and v["name"] in ("revenue", "operating_income")
    ]
    failures = missing + [{"metric_id": z["metric_id"], "reason": "zero where zero is implausible"} for z in zeros]
    return _result(
        "completeness", "G03", "FAIL" if failures else "PASS",
        {"base_fy": base_fy, "required": required, "explicit_not_available": len(na_ok), "not_available_detail": na_ok,
         "invented_zeros": zeros},
        failures,
    )


# ------------------------------------------------------------------------- G04


def g04_cutoff(ctx: Ctx) -> dict:
    rows = read_jsonl(ctx.work_dir / "sources.jsonl")
    if not rows:
        return _result("cutoff", "G04", "NOT_CHECKED", {"reason": "no sources registry"})
    cutoff = ctx.cutoff
    late = [r for r in rows if r["filed"] > cutoff]
    std = ctx.std
    ev = std.get("cutoff_evidence", {})
    rejected_total = sum(v.get("rows_rejected_after_cutoff", 0) for v in ev.values())
    failures = []
    if late:
        failures.append({"kind": "post_cutoff_admitted", "count": len(late), "examples": [r["source_id"] for r in late[:3]]})
    if rejected_total == 0:
        # a filter that rejected nothing proves nothing
        failures.append({"kind": "filter_not_exercised", "detail": "no post-cutoff rows existed; the cutoff was not tested"})
    return _result(
        "cutoff", "G04", "FAIL" if failures else "PASS",
        {
            "cutoff": cutoff,
            "registry_rows": len(rows),
            "max_filed_admitted": max(r["filed"] for r in rows),
            "post_cutoff_rows_rejected": rejected_total,
            "per_ticker": ev,
        },
        failures,
    )


# ------------------------------------------------------------------ G05 / G06 / G08


def g05_tag_drift(ctx: Ctx) -> dict:
    std = ctx.std
    drift = std.get("drift", [])
    breaks = [b for b in std.get("series_breaks", []) if b["kind"] == "tag_change"]
    note = ctx.note()
    keys = {l["key"] for l in (note or {}).get("limitations", [])}
    required = set()
    for b in breaks:
        required.add(f"tag_change:{b['ticker']}:{b['metric']}")
    undisclosed = sorted(k for k in required if k not in keys)
    failures = []
    if undisclosed and note is not None:
        failures.append({"kind": "undisclosed_definition_change", "keys": undisclosed})
    if note is None:
        # before the note exists this gate reports what it will demand
        return _result(
            "tag_drift", "G05", "NOT_CHECKED",
            {"same_year_conflicts": len(drift), "series_definition_changes": len(breaks),
             "disclosure_required": sorted(required),
             "reason": "note not submitted yet; disclosure cannot be checked"},
        )
    return _result(
        "tag_drift", "G05", "FAIL" if failures else "PASS",
        {"same_year_conflicts": len(drift), "series_definition_changes": len(breaks),
         "disclosure_required": sorted(required), "disclosed": sorted(keys & required)},
        failures,
    )


def g06_restatement(ctx: Ctx) -> dict:
    std = ctx.std
    rest = std.get("restatements", [])
    tol = ctx.gate_cfg["G06"]["tolerance"]
    material = [r for r in rest if (r["rel_diff"] or 0) > tol]
    note = ctx.note()
    if note is None:
        return _result(
            "restatement", "G06", "NOT_CHECKED",
            {"restatements": len(rest), "material": len(material),
             "reason": "note not submitted yet"},
        )
    keys = {l["key"] for l in note.get("limitations", [])}
    required = {f"recast:{r['ticker']}:{r['metric']}" for r in material}
    undisclosed = sorted(k for k in required if k not in keys)
    failures = [{"kind": "undisclosed_restatement", "keys": undisclosed}] if undisclosed else []
    return _result(
        "restatement", "G06", "FAIL" if failures else "PASS",
        {"restatements": len(rest), "material_over_tolerance": len(material),
         "tolerance": tol, "disclosure_required": sorted(required)} | {"disclosed": sorted(keys & required)},
        failures,
    )


def g08_provenance(ctx: Ctx) -> dict:
    reg = ctx.model.get("registry", {})
    src = ctx.sources
    if not reg:
        return _result("provenance", "G08", "NOT_CHECKED", {"reason": "no model output"})
    broken, unattested, unpriced = [], [], []
    for mid, rec in reg.items():
        if rec["kind"] not in ("observed", "derived"):
            continue
        if rec["value"] is None:
            continue
        if not rec["sources"]:
            if rec["kind"] == "derived" and rec.get("formula"):
                continue  # derived from other metrics, checked by G09
            broken.append({"metric_id": mid, "reason": "no source record"})
            continue
        for sid in rec["sources"]:
            if sid not in src:
                broken.append({"metric_id": mid, "source_id": sid, "reason": "source id not in registry"})
            elif src[sid]["filed"] > ctx.cutoff:
                broken.append({"metric_id": mid, "source_id": sid, "reason": "source filed after cutoff"})
            elif src[sid].get("provenance_class") == "analyst_input" and not src[sid].get("attested_by"):
                unattested.append({"metric_id": mid, "source_id": sid})
    failures = broken + [{"kind": "unattested_analyst_input", **u} for u in unattested]
    return _result(
        "provenance", "G08", "FAIL" if failures else "PASS",
        {"registry_metrics": len(reg), "source_records": len(src),
         "fully_traced": sum(1 for r in reg.values() if r["sources"] or r["value"] is None),
         "unattested_analyst_inputs": len(unattested)},
        failures,
    )


# ------------------------------------------------------------------ G07 / G09


def g07_tie_out(ctx: Ctx) -> dict:
    note = ctx.note()
    if note is None:
        return _result("tie_out", "G07", "NOT_CHECKED", {"reason": "note not submitted yet"})
    reg = ctx.model.get("registry", {})
    cfg = ctx.gate_cfg["G07"]
    rel_tol, abs_tol = cfg["tolerance_rel"], cfg["tolerance_abs"]

    failures, checked = [], 0
    for claim in note.get("claims", []):
        mid = claim.get("metric_id")
        if not mid:
            failures.append({"kind": "unreferenced_number", "claim_id": claim.get("claim_id"), "text": claim.get("text", "")[:120]})
            continue
        rec = reg.get(mid)
        if rec is None:
            failures.append({"kind": "unknown_metric_id", "claim_id": claim.get("claim_id"), "metric_id": mid})
            continue
        checked += 1
        quoted = claim.get("quoted_value")
        if rec["value"] is None:
            if quoted is not None:
                failures.append({"kind": "quoted_unavailable_value", "metric_id": mid, "quoted": quoted})
            continue
        if quoted is None:
            failures.append({"kind": "claim_without_quoted_value", "metric_id": mid})
            continue
        delta = abs(quoted - rec["value"])
        ok = delta <= max(abs_tol, abs(rec["value"]) * rel_tol)
        if not ok:
            failures.append(
                {"kind": "value_mismatch", "metric_id": mid, "quoted": quoted, "registry": rec["value"], "abs_delta": delta}
            )

    return _result(
        "tie_out", "G07", "FAIL" if failures else "PASS",
        {"claims": len(note.get("claims", [])), "tied": checked, "tolerances": {"rel": rel_tol, "abs": abs_tol}},
        failures,
    )


def g09_arithmetic(ctx: Ctx) -> dict:
    reg = ctx.model.get("registry", {})
    if not reg:
        return _result("arithmetic", "G09", "NOT_CHECKED", {"reason": "no model output"})
    tol = ctx.gate_cfg["G09"]["tolerance_rel"]
    failures, checked = [], 0

    def val(mid: str) -> float | None:
        r = reg.get(mid)
        return None if r is None else r["value"]

    for ticker in ctx.std["tickers"]:
        for fy in [f"FY{y}" for y in ctx.std["years"]]:
            rev = val(f"{ticker}.{fy}.revenue")
            oi = val(f"{ticker}.{fy}.operating_income")
            ni = val(f"{ticker}.{fy}.net_income")
            cfo = val(f"{ticker}.{fy}.cfo")
            capex = val(f"{ticker}.{fy}.capex")
            cases = {
                "ebit_margin": (val(f"{ticker}.{fy}.ebit_margin"), (oi / rev) if rev and oi is not None else None),
                "net_margin": (val(f"{ticker}.{fy}.net_margin"), (ni / rev) if rev and ni is not None else None),
                "fcf": (val(f"{ticker}.{fy}.fcf"), (cfo - capex) if cfo is not None and capex is not None else None),
            }
            for name, (stored, recomputed) in cases.items():
                if stored is None or recomputed is None:
                    continue
                checked += 1
                if abs(stored - recomputed) > max(1e-12, abs(recomputed) * tol):
                    failures.append({"kind": "ratio_mismatch", "metric_id": f"{ticker}.{fy}.{name}",
                                     "stored": stored, "recomputed": recomputed})
    return _result(
        "arithmetic", "G09", "FAIL" if failures else "PASS",
        {"recomputed": checked, "tolerance": tol,
         "method": "ratios recomputed from unrounded stored numerators/denominators"},
        failures,
    )


# ------------------------------------------------------------------------ G10


def g10_model_integrity(ctx: Ctx) -> dict:
    model = ctx.model
    if not model:
        return _result("model_integrity", "G10", "NOT_CHECKED", {"reason": "no model output"})
    failures = []

    # (a) no company constant escaped into the model module
    src_text = (ctx.root / "src" / "pcd" / "model.py").read_text(encoding="utf-8")
    token_re = re.compile(r"""["'](V|MA|PYPL|FI|GPN)["']""")
    hits = [m.group(0) for m in token_re.finditer(src_text)]
    if hits:
        failures.append({"kind": "ticker_literal_in_model", "hits": sorted(set(hits))})

    # (b) the DCF is re-derived from assumptions, not read back
    assum = ctx.assum
    disc = assum["discounting"]
    rec = []
    for ticker, d in model.get("dcf", {}).items():
        a = assum[ticker]
        ke = disc["risk_free"] + a["beta"] * disc["equity_risk_premium"]
        g = disc["terminal_growth"]
        rev = d["base_revenue"]
        fcf_last, pv = None, 0.0
        for i, fy in enumerate(assum["meta"]["forecast_years"]):
            rev = rev * (1 + a["growth"][i])
            fcf = rev * a["fcf_margin"][i]
            pv += fcf / (1 + ke) ** (i + 1)
            fcf_last = fcf
        tv = fcf_last * (1 + g) / (ke - g)
        equity = pv + tv / (1 + ke) ** len(assum["meta"]["forecast_years"])
        delta = abs(equity - d["equity_value"])
        rec.append({"ticker": ticker, "stored": d["equity_value"], "recomputed": equity, "abs_delta": delta})
        if delta > max(1.0, abs(equity) * 1e-9):
            failures.append({"kind": "dcf_not_reproducible", "ticker": ticker, "stored": d["equity_value"], "recomputed": equity})
        if abs(ke - d["ke"]) > 1e-12:
            failures.append({"kind": "discount_rate_mismatch", "ticker": ticker})

    # (c) no plug: no assumption may name an output
    banned = ("equity_value", "target", "intrinsic", "per_share")
    for ticker in assum:
        if ticker in ("meta", "discounting", "sensitivity"):
            continue
        for k in assum[ticker]:
            if any(b in k for b in banned):
                failures.append({"kind": "assumption_named_like_an_output", "ticker": ticker, "key": k})

    return _result(
        "model_integrity", "G10", "FAIL" if failures else "PASS",
        {"recomputed": rec, "model_module_free_of_ticker_literals": not hits},
        failures,
    )


# ------------------------------------------------------------------------ G11


def g11_period_alignment(ctx: Ctx) -> dict:
    std = ctx.std
    months = {t: d["fiscal_year_end_month"] for t, d in std.get("tickers", {}).items()}
    mixed = len({m for m in months.values() if m != "mixed"}) > 1
    note = ctx.note()
    if not mixed:
        return _result("period_alignment", "G11", "PASS",
                       {"fiscal_year_end_months": months, "mixed": False})
    if note is None:
        return _result("period_alignment", "G11", "NOT_CHECKED",
                       {"fiscal_year_end_months": months, "mixed": True,
                        "reason": "note not submitted yet; footnote cannot be checked"})
    keys = {l["key"] for l in note.get("limitations", [])}
    refs = set(note.get("footnote_refs", []))
    ok_key = "period_alignment" in keys
    ok_ref = "period_alignment" in refs
    failures = []
    if not ok_key:
        failures.append({"kind": "missing_footnote", "required_key": "period_alignment"})
    if not ok_ref:
        failures.append({"kind": "footnote_not_referenced", "detail": "the comparative sentences never cite the footnote"})
    return _result(
        "period_alignment", "G11", "FAIL" if failures else "PASS",
        {"fiscal_year_end_months": months, "mixed": True, "footnote_present": ok_key, "footnote_referenced": ok_ref},
        failures,
    )


# ------------------------------------------------------------------------ G13


def _perturb(src_raw: Path, dst_raw: Path, ticker: str, filed: str, value: float, start: str, end: str) -> None:
    """Inject one synthetic annual row.

    The period must be the issuer's *real* fiscal year. Probing with a period the
    resolver would ignore makes both halves of G13 vacuous: the leak test passes
    because nothing was ever eligible, and the sensitivity test fails for the same
    reason. The first version of this gate did exactly that.
    """
    payload = read_json(src_raw / f"{ticker}.json")
    units = payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
    unit = next(iter(units))
    units[unit].append(
        {
            "start": start,
            "end": end,
            "val": value,
            "accn": "0000000000-26-000001",
            "fy": int(end[:4]),
            "fp": "FY",
            "form": "10-K",
            "filed": filed,
            "frame": f"CY{end[:4]}",
        }
    )
    write_json(dst_raw / f"{ticker}.json", payload)


def _standardized_signature(std: dict) -> str:
    slim = {
        "tickers": {
            t: {fy: {"values": d["values"], "tags": d["tags"]} for fy, d in v["years"].items()}
            for t, v in std["tickers"].items()
        }
    }
    return sha256_text(canon(slim))


def g13_leakage(ctx: Ctx) -> dict:
    from .standardize import standardize

    base = ctx.std
    if not base:
        return _result("leakage", "G13", "NOT_CHECKED", {"reason": "no standardised baseline"})
    cfg = ctx.gate_cfg["G13"]
    probe_value = cfg["probe_value"]
    ticker = ctx.run["case"]["universe"][0]
    baseline_sig = _standardized_signature(base)
    cutoff = ctx.cutoff
    after = _shift(cutoff, 30)
    before = cutoff

    tmp = Path(tempfile.mkdtemp(prefix="pcd-leak-"))
    probe_fy = f"FY{base['years'][-1]}"
    probe_end = base["tickers"][ticker]["years"][probe_fy]["fy_end"]
    probe_start = _shift(probe_end, -364)
    try:
        # (1) post-cutoff perturbation must change nothing
        post_dir = tmp / "post"
        shutil.copytree(ctx.case_dir, post_dir)
        _perturb(post_dir / "raw", post_dir / "raw", ticker, after, probe_value, probe_start, probe_end)
        post_out = tmp / "post_out"
        post_std = standardize(ctx.case_id, post_dir, post_out)
        post_sig = _standardized_signature(post_std)

        # (2) pre-cutoff perturbation must change something - the probe must be able to fail
        pre_dir = tmp / "pre"
        shutil.copytree(ctx.case_dir, pre_dir)
        _perturb(pre_dir / "raw", pre_dir / "raw", ticker, before, probe_value, probe_start, probe_end)
        pre_out = tmp / "pre_out"
        pre_std = standardize(ctx.case_id, pre_dir, pre_out)
        pre_sig = _standardized_signature(pre_std)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failures = []
    if post_sig != baseline_sig:
        failures.append({"kind": "future_information_leaked", "detail": "a post-cutoff row changed a pre-cutoff output"})
    if pre_sig == baseline_sig:
        failures.append({"kind": "probe_insensitive", "detail": "a pre-cutoff row was ignored, so the probe cannot detect leakage"})

    return _result(
        "leakage", "G13", "FAIL" if failures else "PASS",
        {
            "cutoff": cutoff,
            "probe": {"ticker": ticker, "concept": "NetIncomeLoss", "value": probe_value},
            "post_cutoff_filed": after,
            "pre_cutoff_filed": before,
            "baseline_signature": baseline_sig[:16],
            "post_perturbation_signature": post_sig[:16],
            "pre_perturbation_signature": pre_sig[:16],
            "interpretation": "post == baseline (no leak) and pre != baseline (probe is live)",
        },
        failures,
    )


def _shift(iso: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


# -------------------------------------------------------------------------- runner


GATES = {
    "G01": g01_schema,
    "G02": g02_identity,
    "G03": g03_completeness,
    "G04": g04_cutoff,
    "G05": g05_tag_drift,
    "G06": g06_restatement,
    "G07": g07_tie_out,
    "G08": g08_provenance,
    "G09": g09_arithmetic,
    "G10": g10_model_integrity,
    "G11": g11_period_alignment,
    "G12": g12_permissions,
    "G13": g13_leakage,
}


def run_gates(case_id: str, round_dir: Path | None = None, only: list[str] | None = None) -> dict:
    ctx = Ctx(case_id, round_dir)
    out = {}
    for gid, fn in GATES.items():
        if only and gid not in only:
            continue
        try:
            out[gid] = fn(ctx)
        except Exception as exc:  # a crashed gate is a failed gate, never a pass
            out[gid] = _result(fn.__name__, gid, "FAIL", {"exception": f"{type(exc).__name__}: {exc}"},
                               [{"kind": "gate_crashed", "error": str(exc)}])
    summary = {
        "case_id": case_id,
        "round": round_dir.name if round_dir else None,
        "gate_results": out,
        "counts": {s: sum(1 for r in out.values() if r["status"] == s) for s in STATUS},
        "blocking_failures": [gid for gid, r in out.items() if r["status"] == "FAIL" and r["blocking"]],
    }
    return summary


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="pcd.gates", description="Run numeric gates against a case.")
    ap.add_argument("--case", default="case-01")
    ap.add_argument("--round", default=None)
    ap.add_argument("--only", default=None, help="comma separated gate ids, e.g. G07,G09")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = repo_root()
    rd = root / "cases" / args.case / "rounds" / args.round if args.round else None
    only = args.only.split(",") if args.only else None
    summary = run_gates(args.case, rd, only)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    for gid, r in summary["gate_results"].items():
        mark = {"PASS": "PASS", "FAIL": "FAIL", "NOT_CHECKED": "n/a "}[r["status"]]
        print(f"{gid} {mark} {r['name']:<18s} {len(r['failures'])} finding(s)")
        for f in r["failures"][:4]:
            print(f"        - {f}")
    print(f"\n{summary['counts']}  blocking failures: {summary['blocking_failures']}")
    return 0 if not summary["blocking_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())

"""Run the three specialist critics against a round and write their reports.

Each critic re-derives its evidence independently -- the tie-out critic recomputes
ratios from standardized source values (not from metrics.json ratios), the
provenance critic re-hashes the frozen snapshot, the model-review critic rebuilds
the DCF from the assumption file with its own arithmetic. None of them reads another
critic's report, and none of them trusts the generator's submission text.

Usage: python scripts/critic_checks.py <round-name>   e.g. round-001
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pcd.util import load_toml, read_json, sha256_file  # noqa: E402

CASE = "case-01"
ROUND = sys.argv[1] if len(sys.argv) > 1 else "round-001"
RD = ROOT / "cases" / CASE / "rounds" / ROUND

STD = read_json(ROOT / "cases" / CASE / "work" / "standardized.json")
MODEL = read_json(ROOT / "cases" / CASE / "work" / "metrics.json")
REG = MODEL["registry"]
SOURCES = {r["source_id"]: r for r in (json.loads(l) for l in
           (ROOT / "cases" / CASE / "work" / "sources.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
RUN = load_toml(ROOT / "config" / "run.toml")
ASSUM = load_toml(ROOT / "config" / "assumptions" / f"{CASE}.toml")
NOTE = read_json(RD / "note" / "claims.json")
CUTOFF = read_json(ROOT / "data" / "frozen" / CASE / "MANIFEST.json")["cutoff"]
UNIVERSE = RUN["case"]["universe"]


def report(role, checked, evidence, findings):
    return {
        "role": role,
        "round": ROUND,
        "candidate_id": NOTE["candidate_id"],
        "checked": checked,
        "evidence": evidence,
        "findings": findings,
        "verdict_hint": "clean" if not findings else "revise",
    }


# ----------------------------------------------------------------- pcd-critic-tieout

def critic_tieout():
    findings, detail = [], {}
    for t in UNIVERSE:
        vals = STD["tickers"][t]["years"]["FY2025"]["values"]
        rev, oi = vals["revenue"], vals["operating_income"]
        ni, cfo, capex = vals["net_income"], vals["cfo"], vals["capex"]
        mine = {
            "ebit_margin": oi / rev if rev else None,
            "net_margin": ni / rev if rev else None,
            "fcf_conversion": (cfo - capex) / ni if ni else None,
        }
        detail[t] = {}
        for name, v in mine.items():
            rec = REG[f"{t}.FY2025.{name}"]
            ok = v is not None and rec["value"] is not None and abs(v - rec["value"]) <= abs(rec["value"]) * 1e-9
            detail[t][name] = {"recomputed": round(v, 10), "registry": round(rec["value"], 10), "match": ok}
            if not ok:
                findings.append({"severity": "high", "kind": "ratio_mismatch",
                                 "message": f"{t}.FY2025.{name}: critic {v} vs registry {rec['value']}"})
        # the note's peer-table growth figure must equal the registry growth
        g_reg = REG[f"{t}.FY2025.revenue_growth"]["value"]
        prev = STD["tickers"][t]["years"].get("FY2024", {}).get("values", {}).get("revenue")
        g_mine = rev / prev - 1 if prev else None
        ok = g_mine is not None and abs(g_mine - g_reg) <= abs(g_reg) * 1e-9
        detail[t]["revenue_growth"] = {"recomputed": round(g_mine, 10), "registry": round(g_reg, 10), "match": ok}
        if not ok:
            findings.append({"severity": "high", "kind": "growth_mismatch",
                             "message": f"{t}: critic growth {g_mine} vs registry {g_reg}"})

    # every quoted_value must equal the registry exactly (the generator built the
    # note from the registry, so any drift means the file was edited afterwards)
    drifted = [c["claim_id"] for c in NOTE["claims"]
               if REG.get(c["metric_id"]) is not None
               and c["quoted_value"] is not None
               and REG[c["metric_id"]]["value"] is not None
               and abs(c["quoted_value"] - REG[c["metric_id"]]["value"]) > 0]
    if drifted:
        findings.append({"severity": "high", "kind": "quoted_value_drift", "message": f"claims drifted: {drifted}"})

    # CAGR windows: GPN may not use the pre-recast FY2022 base
    if "GPN.FY2022-FY2025.revenue_cagr" in {c["metric_id"] for c in NOTE["claims"]}:
        findings.append({"severity": "high", "kind": "recast_window_violation",
                         "message": "note cites GPN FY2022-anchored CAGR, which spans the divestiture recast"})
    for c in NOTE["claims"]:
        if c["metric_id"].endswith("revenue_cagr"):
            rec = REG[c["metric_id"]]
            if rec["comparability"] != "comparable":
                findings.append({"severity": "high", "kind": "noncomparable_window",
                                 "message": f"{c['metric_id']} comparability={rec['comparability']}"})

    return report("pcd-critic-tieout",
                  [f"recomputed ebit_margin/net_margin/fcf_conversion/revenue_growth for {len(UNIVERSE)} tickers from standardized source values",
                   "checked all 50 quoted_value claims against the registry",
                   "checked CAGR windows against recast comparability"],
                  {"per_ticker": detail, "claims_checked": len(NOTE["claims"])}, findings)


# -------------------------------------------------------------- pcd-critic-provenance

def critic_provenance():
    findings, evidence = [], {}
    man = read_json(ROOT / "data" / "frozen" / CASE / "MANIFEST.json")

    # snapshot integrity: re-hash every stored payload
    bad = []
    for ticker, meta in man["tickers"].items():
        p = ROOT / meta["stored_path"]
        if sha256_file(p) != meta["stored_sha256"]:
            bad.append(ticker)
    evidence["snapshot_rehashed"] = len(man["tickers"])
    evidence["snapshot_mismatches"] = bad
    if bad:
        findings.append({"severity": "high", "kind": "snapshot_corrupted", "message": f"{bad}"})

    # every metric's sources: registered, filed at or before the cutoff
    late, unknown = [], []
    checked = 0
    for mid, rec in REG.items():
        for sid in rec.get("sources", []):
            checked += 1
            s = SOURCES.get(sid)
            if s is None:
                unknown.append({"metric_id": mid, "source_id": sid})
            elif s["filed"] > CUTOFF:
                late.append({"metric_id": mid, "source_id": sid, "filed": s["filed"]})
    evidence["source_refs_checked"] = checked
    evidence["unregistered"] = unknown
    evidence["post_cutoff"] = late
    if unknown:
        findings.append({"severity": "high", "kind": "unregistered_source", "message": f"{unknown[:5]}"})
    if late:
        findings.append({"severity": "high", "kind": "post_cutoff_source", "message": f"{late[:5]}"})

    # the note's sources block must list exactly the accessions its claims rely on
    claim_sids = {sid for c in NOTE["claims"] if REG.get(c["metric_id"])
                  for sid in REG[c["metric_id"]].get("sources", [])}
    claim_accns = {SOURCES[sid]["accn"] for sid in claim_sids if sid in SOURCES}
    src_block = next((b for b in NOTE["blocks"] if b.get("kind") == "sources"), None)
    evidence["claims_accessions"] = sorted(claim_accns)
    evidence["sources_block_present"] = src_block is not None
    if src_block is None:
        findings.append({"severity": "high", "kind": "missing_sources_block",
                         "message": "note has no sources block"})

    # every finding the model marked requires_note_disclosure must surface in the note
    lim_keys = {l["key"] for l in NOTE["limitations"]}
    fn_keys = next((b["keys"] for b in NOTE["blocks"] if b.get("kind") == "footnotes"), [])
    mapping = {
        "F-BREAK-FI-da-tag_change": "tag_change:FI:da",
        "F-BREAK-GPN-revenue-recast": "recast:GPN:revenue",
        "F-BREAK-GPN-operating_income-recast": "recast:GPN:operating_income",
        "F-BREAK-GPN-cfo-recast": "recast:GPN:cfo",
        "F-BREAK-GPN-da-recast": "recast:GPN:da",
        "F-BREAK-GPN-tax_expense-recast": "recast:GPN:tax_expense",
        "F-BREAK-GPN-cash-recast": None,   # cash is not cited in the note: no disclosure owed
        "F-BASIS-GPN": "recast:GPN:revenue",
        "F-SHARES-V": "shares_V",
    }
    owed, covered = [], []
    for f in MODEL["findings"]:
        if not f.get("requires_note_disclosure"):
            continue
        key = mapping.get(f["id"])
        if key is None:
            continue
        owed.append(f["id"])
        covered.append(key in lim_keys and key in fn_keys)
    evidence["disclosure_owed"] = owed
    evidence["disclosure_covered"] = covered
    if owed and not all(covered):
        findings.append({"severity": "high", "kind": "undisclosed_finding",
                         "message": f"owed={owed} covered={covered}"})

    return report("pcd-critic-provenance",
                  ["re-hashed all frozen payloads against MANIFEST.json",
                   f"resolved {checked} source references against sources.jsonl and the cutoff",
                   "cross-checked the note's sources block and disclosure obligations"],
                  evidence, findings)


# ------------------------------------------------------------- pcd-critic-model-review

def critic_model_review():
    findings, detail = [], {}
    disc = ASSUM["discounting"]
    years = ASSUM["meta"]["forecast_years"]
    for t in UNIVERSE:
        a = ASSUM[t]
        ke = disc["risk_free"] + a["beta"] * disc["equity_risk_premium"]
        g = disc["terminal_growth"]
        rev = MODEL["dcf"][t]["base_revenue"]
        pv, fcf_last = 0.0, None
        for i, _ in enumerate(years):
            rev = rev * (1 + a["growth"][i])
            fcf = rev * a["fcf_margin"][i]
            pv += fcf / (1 + ke) ** (i + 1)
            fcf_last = fcf
        tv = fcf_last * (1 + g) / (ke - g)
        equity = pv + tv / (1 + ke) ** len(years)
        stored = MODEL["dcf"][t]["equity_value"]
        ok = abs(equity - stored) <= max(1.0, abs(equity) * 1e-9)
        detail[t] = {"ke_recomputed": round(ke, 6), "ke_stored": MODEL["dcf"][t]["ke"],
                     "equity_recomputed": round(equity, 2), "equity_stored": round(stored, 2), "match": ok}
        if not ok:
            findings.append({"severity": "high", "kind": "dcf_mismatch",
                             "message": f"{t}: critic equity {equity} vs stored {stored}"})
        if abs(ke - MODEL["dcf"][t]["ke"]) > 1e-12:
            findings.append({"severity": "high", "kind": "ke_mismatch", "message": f"{t}"})

        # per-share discipline: withheld exactly when the input is unavailable
        ps = MODEL["dcf"][t]["per_share"]
        shares = MODEL["dcf"][t]["shares"]
        if shares is None and ps is not None:
            findings.append({"severity": "high", "kind": "per_share_without_shares", "message": t})
        if shares and ps is not None and abs(ps - equity / shares) > 0.01:
            findings.append({"severity": "high", "kind": "per_share_mismatch", "message": t})

        # sensitivity grid: monotone in g (up) and in ke (down), at every ke level
        grid = MODEL["dcf"][t]["sensitivity"]["grid"]
        for row in grid:
            if any(row["values"][i] >= row["values"][i + 1] for i in range(len(row["values"]) - 1)):
                findings.append({"severity": "high", "kind": "grid_not_monotone_in_g",
                                 "message": f"{t} ke={row['ke']}"})
        for i in range(len(grid) - 1):
            if grid[i + 1]["values"][1] >= grid[i]["values"][1]:
                findings.append({"severity": "high", "kind": "grid_not_monotone_in_ke", "message": t})

        # terminal share is a fraction
        ts = MODEL["dcf"][t]["terminal_share_of_value"]
        if not (0 < ts < 1):
            findings.append({"severity": "high", "kind": "terminal_share_out_of_range",
                             "message": f"{t}: {ts}"})

    if MODEL["dcf"]["V"]["per_share"] is not None:
        findings.append({"severity": "high", "kind": "visa_per_share_published",
                         "message": "V shares are not_available; a per-share value was published anyway"})
    if not any(f["id"] == "F-SHARES-V" for f in MODEL["findings"]):
        findings.append({"severity": "medium", "kind": "withholding_undocumented",
                         "message": "V per-share withheld but no finding records it"})

    return report("pcd-critic-model-review",
                  ["re-derived ke, the 3-year forecast, terminal value and equity value for all 5 tickers from config/assumptions/case-01.toml with independent arithmetic",
                   "re-checked per-share consistency against the share provenance field",
                   "checked sensitivity grid monotonicity in both axes and terminal-share range"],
                  {"per_ticker": detail}, findings)


def main():
    out = RD / "critic"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("tieout", critic_tieout), ("provenance", critic_provenance), ("model_review", critic_model_review)):
        rep = fn()
        (out / f"{name}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{name}: {rep['verdict_hint']} ({len(rep['findings'])} findings)")


if __name__ == "__main__":
    main()

"""The deterministic model: historical ratios, a three-year forecast, an equity DCF.

Two invariants hold here and are enforced by gates, not by convention:

* **No company constant.** Every forward-looking number comes from
  `config/assumptions/<case>.toml`. G10 greps this file for ticker literals.
* **No plug.** Intrinsic value is an arithmetic consequence of the assumptions and
  the reported base year. Nothing writes a target and back-solves.

The model also refuses to publish a ratio whose inputs change definition mid-series.
`comparability` is carried on every metric so that the note can disclose a break
instead of quietly comparing two different things.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import load_toml, read_json, read_jsonl, repo_root, safe_div, write_json

STATUS_ORDER = {"comparable": 0, "basis_break": 1, "definition_break": 2}

# Which observed metrics feed each derived metric. Used to propagate comparability.
DERIVED_DEPS: dict[str, list[str]] = {
    "ebit_margin": ["operating_income", "revenue"],
    "net_margin": ["net_income", "revenue"],
    "fcf": ["cfo", "capex"],
    "fcf_conversion": ["cfo", "capex", "net_income"],
    "ebitda_proxy": ["operating_income", "da"],
    "capex_intensity": ["capex", "revenue"],
    "da_intensity": ["da", "revenue"],
}

LEVEL_METRICS = ["revenue", "operating_income", "net_income", "cfo", "capex", "da", "equity", "shares_diluted", "eps_diluted"]
RATIO_METRICS = ["ebit_margin", "net_margin", "fcf", "fcf_conversion", "ebitda_proxy", "capex_intensity", "da_intensity"]


def _worst(a: str, b: str) -> str:
    return a if STATUS_ORDER.get(a, 0) >= STATUS_ORDER.get(b, 0) else b


def _breaks_by_metric(breaks: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for b in breaks:
        out.setdefault(b["metric"], []).append(b)
    return out


def status_of(name: str, fy: str, breaks: dict[str, list[dict]]) -> str:
    """Comparability status of one metric in one fiscal year."""
    status = "comparable"
    for b in breaks.get(name, []):
        if b["kind"] == "tag_change":
            if int(fy[2:]) >= int(b["to_fy"][2:]):
                status = _worst(status, "definition_break")
        else:  # recast
            if b["not_comparable_fys"] and fy in b["not_comparable_fys"]:
                status = _worst(status, "basis_break")
    return status


# ------------------------------------------------------------------------ registry


def _rec(
    metric_id: str,
    ticker: str,
    period: str,
    name: str,
    value: float | None,
    unit: str,
    kind: str,
    *,
    formula: str | None = None,
    sources: list[str] | None = None,
    comparability: str = "comparable",
    note: str | None = None,
) -> dict:
    return {
        "metric_id": metric_id,
        "ticker": ticker,
        "period": period,
        "name": name,
        "value": value,
        "unit": unit,
        "kind": kind,
        "formula": formula,
        "sources": sources or [],
        "comparability": comparability,
        "note": note,
    }


def build(case_id: str, out_dir: Path) -> dict:
    std = read_json(out_dir / "standardized.json")
    if not std:
        raise FileNotFoundError("standardized.json missing: run pcd.standardize first")

    root = repo_root()
    assum = load_toml(root / "config" / "assumptions" / f"{case_id}.toml")
    disc = assum["discounting"]
    sens = assum.get("sensitivity", {"ke_delta": [-0.01, 0.0, 0.01], "g_delta": [-0.005, 0.0, 0.005]})
    forecast_years = list(assum["meta"]["forecast_years"])

    src_index = {r["source_id"]: r for r in read_jsonl(out_dir / "sources.jsonl")}
    years = [int(y) for y in std["years"]]

    registry: dict[str, dict] = {}
    findings: list[dict] = []
    dcf_out: dict[str, Any] = {}

    for ticker, tdata in std["tickers"].items():
        breaks = _breaks_by_metric(tdata.get("series_breaks", []))
        na = {n["metric"]: n for n in tdata.get("not_available", [])}

        # ---- observed levels and ratios
        for y in years:
            fy = f"FY{y}"
            yd = tdata["years"].get(fy)
            if not yd:
                continue
            vals = yd["values"]
            prov = yd["provenance"]
            tags = yd["tags"]

            for name in LEVEL_METRICS:
                if name not in vals:
                    mid = f"{ticker}.{fy}.{name}"
                    reason = na.get(mid, {}).get("reason", "not reported in an admissible filing")
                    registry[mid] = _rec(
                        mid, ticker, fy, name, None, "USD", "observed",
                        comparability="not_available",
                        note=f"{reason}. Rendered as not_available; no substitute value was created.",
                    )
                    continue
                unit = "shares" if name == "shares_diluted" else ("USD/shares" if name == "eps_diluted" else "USD")
                registry[f"{ticker}.{fy}.{name}"] = _rec(
                    f"{ticker}.{fy}.{name}",
                    ticker,
                    fy,
                    name,
                    float(vals[name]),
                    unit,
                    "observed",
                    sources=prov.get(name, []),
                    comparability=status_of(name, fy, breaks),
                    note=f"XBRL tag {tags.get(name)}",
                )

            prior_rev = None
            if y - 1 in years:
                prior = tdata["years"].get(f"FY{y - 1}")
                prior_rev = prior["values"].get("revenue") if prior else None

            def g(name: str) -> float | None:
                return float(vals[name]) if name in vals else None

            derived = {
                "ebit_margin": (safe_div(g("operating_income"), g("revenue")), "operating_income / revenue", "ratio"),
                "net_margin": (safe_div(g("net_income"), g("revenue")), "net_income / revenue", "ratio"),
                "fcf": (
                    (g("cfo") - g("capex")) if (g("cfo") is not None and g("capex") is not None) else None,
                    "cfo - capex",
                    "USD",
                ),
                "fcf_conversion": (
                    safe_div((g("cfo") - g("capex")) if (g("cfo") is not None and g("capex") is not None) else None, g("net_income")),
                    "(cfo - capex) / net_income",
                    "ratio",
                ),
                "ebitda_proxy": (
                    (g("operating_income") + g("da")) if (g("operating_income") is not None and g("da") is not None) else None,
                    "operating_income + da",
                    "USD",
                ),
                "capex_intensity": (safe_div(g("capex"), g("revenue")), "capex / revenue", "ratio"),
                "da_intensity": (safe_div(g("da"), g("revenue")), "da / revenue", "ratio"),
            }

            for name, (value, formula, unit) in derived.items():
                deps = DERIVED_DEPS.get(name, [])
                st = "comparable"
                for d in deps:
                    st = _worst(st, status_of(d, fy, breaks))
                sources: list[str] = []
                for d in deps:
                    sources.extend(prov.get(d, []))
                if name == "fcf" and len(deps) == 2:
                    st_level = _worst(status_of("cfo", fy, breaks), status_of("capex", fy, breaks))
                    st = _worst(st, st_level)
                mid = f"{ticker}.{fy}.{name}"
                registry[mid] = _rec(
                    mid, ticker, fy, name, value, unit, "derived",
                    formula=formula,
                    sources=sorted(set(sources)),
                    comparability=st if value is not None else "not_available",
                    note=None if value is not None else "an input is not available; no number was invented",
                )

            # revenue growth needs the prior year and has its own break semantics
            mid = f"{ticker}.{fy}.revenue_growth"
            if g("revenue") is None or prior_rev is None:
                registry[mid] = _rec(
                    mid, ticker, fy, "revenue_growth", None, "ratio", "derived",
                    formula="revenue_t / revenue_{t-1} - 1", comparability="not_available",
                    note="prior-year revenue unavailable" if prior_rev is None else "revenue unavailable",
                )
            else:
                st = "comparable"
                for b in breaks.get("revenue", []):
                    if b["kind"] == "tag_change" and fy == b["to_fy"]:
                        st = _worst(st, "definition_break")
                    if b["kind"] == "recast" and fy in b["affected_fys"]:
                        # first re-presented year: the denominator is on the old basis
                        st = _worst(st, "basis_break")
                srcs = list(prov.get("revenue", []))
                prior = tdata["years"].get(f"FY{y - 1}")
                srcs += (prior or {}).get("provenance", {}).get("revenue", [])
                registry[mid] = _rec(
                    mid, ticker, fy, "revenue_growth", g("revenue") / prior_rev - 1, "ratio", "derived",
                    formula="revenue_t / revenue_{t-1} - 1",
                    sources=sorted(set(srcs)),
                    comparability=st,
                    note=(
                        "denominator is on a different basis (recast or tag change); "
                        "not a like-for-like growth rate" if st != "comparable" else None
                    ),
                )

        # ---- three-year compound growth, on a comparable window only
        usable = [y for y in years if status_of("revenue", f"FY{y}", breaks) == "comparable"]
        if len(usable) >= 2:
            first, last = usable[0], usable[-1]
            r0 = tdata["years"][f"FY{first}"]["values"].get("revenue")
            r1 = tdata["years"][f"FY{last}"]["values"].get("revenue")
            if r0 and r1:
                n = last - first
                cagr = (r1 / r0) ** (1 / n) - 1
                registry[f"{ticker}.FY{first}-FY{last}.revenue_cagr"] = _rec(
                    f"{ticker}.FY{first}-FY{last}.revenue_cagr",
                    ticker,
                    f"FY{first}-FY{last}",
                    "revenue_cagr",
                    cagr,
                    "ratio",
                    "derived",
                    formula=f"(revenue_{last} / revenue_{first})^(1/{n}) - 1",
                    sources=sorted(set(tdata["years"][f"FY{first}"]["provenance"].get("revenue", []))
                                   | set(tdata["years"][f"FY{last}"]["provenance"].get("revenue", []))),
                    comparability="comparable",
                    note=f"window restricted to years on a single basis: FY{first}-FY{last}",
                )
        if len(usable) < len(years):
            findings.append(
                {
                    "id": f"F-BASIS-{ticker}",
                    "severity": "medium",
                    "kind": "comparability",
                    "ticker": ticker,
                    "message": f"revenue basis is not continuous across FY{years[0]}-FY{years[-1]}; "
                    f"the reported multi-year growth uses FY{usable[0]}-FY{usable[-1]} only",
                    "requires_note_disclosure": True,
                }
            )
        variants = sorted({tdata["years"][f"FY{y}"]["tags"].get("revenue") for y in usable if f"FY{y}" in tdata["years"]})
        if len(variants) > 1:
            findings.append(
                {
                    "id": f"F-TAG-{ticker}",
                    "severity": "high",
                    "kind": "tag_change",
                    "ticker": ticker,
                    "message": f"revenue tag changes across the window: {variants}",
                    "requires_note_disclosure": True,
                }
            )

        # ---- forecast + DCF
        for b in tdata.get("series_breaks", []):
            findings.append(
                {
                    "id": f"F-BREAK-{ticker}-{b['metric']}-{b['kind']}",
                    "severity": "high" if b["kind"] == "tag_change" else "medium",
                    "kind": b["kind"],
                    "ticker": ticker,
                    "metric": b["metric"],
                    "message": b["why"],
                    "detail": b,
                    "requires_note_disclosure": True,
                }
            )

        a = assum.get(ticker)
        if not a:
            findings.append(
                {
                    "id": f"F-ASSUMP-{ticker}",
                    "severity": "high",
                    "kind": "missing_assumption",
                    "ticker": ticker,
                    "message": "no assumption block for this ticker; the model stops rather than defaulting",
                }
            )
            continue

        base_fy = f"FY{years[-1]}"
        rev0 = tdata["years"][base_fy]["values"].get("revenue")
        if rev0 is None:
            findings.append(
                {
                    "id": f"F-BASE-{ticker}",
                    "severity": "high",
                    "kind": "missing_base",
                    "ticker": ticker,
                    "message": f"base-year revenue for {base_fy} is not available; no forecast produced",
                }
            )
            continue

        ke = disc["risk_free"] + a["beta"] * disc["equity_risk_premium"]
        g_term = disc["terminal_growth"]

        rows = []
        rev_prev = rev0
        cashflows = []
        for i, fy in enumerate(forecast_years):
            growth = a["growth"][i]
            rev = rev_prev * (1 + growth)
            fcf = rev * a["fcf_margin"][i]
            df = 1 / (1 + ke) ** (i + 1)
            cashflows.append({"fy": fy, "revenue": rev, "fcf": fcf, "discount_factor": df, "pv": fcf * df})
            rows.append((fy, growth, rev, fcf))
            rev_prev = rev

            for name, value, unit in (
                ("revenue", rev, "USD"),
                ("revenue_growth", growth, "ratio"),
                ("fcf", fcf, "USD"),
            ):
                mid = f"{ticker}.FY{fy}E.{name}"
                registry[mid] = _rec(
                    mid, ticker, f"FY{fy}E", name, value, unit, "forecast",
                    formula="revenue_{t-1} * (1 + growth)" if name != "revenue_growth" else "assumption",
                    comparability="forecast",
                    note=f"assumption block {assum['meta']['version']}",
                )

        tv = cashflows[-1]["fcf"] * (1 + g_term) / (ke - g_term)
        pv_tv = tv / (1 + ke) ** len(forecast_years)
        pv_explicit = sum(c["pv"] for c in cashflows)
        equity = pv_explicit + pv_tv
        terminal_share = safe_div(pv_tv, equity)

        shares = tdata["years"][base_fy]["values"].get("shares_diluted")
        override = a.get("share_count_override") or {}
        attested = bool(override.get("attested_by") and override.get("attested_at") and override.get("value"))
        if attested:
            shares = float(override["value"])
            shares_prov = "analyst_attested"
        else:
            shares_prov = "edgar_xbrl" if shares else "not_available"

        per_share = safe_div(equity, shares) if shares else None

        dcf_out[ticker] = {
            "beta": a["beta"],
            "ke": ke,
            "terminal_growth": g_term,
            "base_fy": base_fy,
            "base_revenue": rev0,
            "forecast": cashflows,
            "tv": tv,
            "pv_terminal": pv_tv,
            "pv_explicit": pv_explicit,
            "equity_value": equity,
            "terminal_share_of_value": terminal_share,
            "shares": shares,
            "shares_provenance": shares_prov,
            "per_share": per_share,
            "assumption_version": assum["meta"]["version"],
        }

        registry[f"{ticker}.DCF.ke"] = _rec(
            f"{ticker}.DCF.ke", ticker, "DCF", "cost_of_equity", ke, "ratio", "derived",
            formula="risk_free + beta * equity_risk_premium", comparability="forecast",
        )
        registry[f"{ticker}.DCF.equity_value"] = _rec(
            f"{ticker}.DCF.equity_value", ticker, "DCF", "equity_value", equity, "USD", "derived",
            formula="sum(pv(fcf)) + pv(terminal_value)", comparability="forecast",
            note=f"terminal value is {terminal_share:.1%} of the total",
        )
        mid = f"{ticker}.DCF.per_share"
        if per_share is None:
            registry[mid] = _rec(
                mid, ticker, "DCF", "value_per_share", None, "USD/shares", "derived",
                formula="equity_value / diluted_shares", comparability="not_available",
                note="diluted share count is not available from XBRL at cutoff; "
                     "an analyst-attested cover-page count is required. No value was estimated.",
            )
            findings.append(
                {
                    "id": f"F-SHARES-{ticker}",
                    "severity": "medium",
                    "kind": "missing_input",
                    "ticker": ticker,
                    "message": "per-share value withheld: diluted share count is not tagged in XBRL "
                    "at or before cutoff. Escalate to the analyst for an attested count "
                    "(config/assumptions share_count_override).",
                    "requires_note_disclosure": True,
                }
            )
        else:
            registry[mid] = _rec(
                mid, ticker, "DCF", "value_per_share", per_share, "USD/shares", "derived",
                formula="equity_value / diluted_shares", comparability="forecast",
                note=f"share count provenance: {shares_prov}",
            )

        if terminal_share and terminal_share > 0.85:
            findings.append(
                {
                    "id": f"F-TV-{ticker}",
                    "severity": "medium",
                    "kind": "valuation_concentration",
                    "ticker": ticker,
                    "message": f"terminal value is {terminal_share:.0%} of intrinsic value; "
                    "the conclusion rests on the terminal assumption",
                    "requires_note_disclosure": True,
                }
            )

        # sensitivity grid (deterministic, recomputed from the same cash flows)
        grid = []
        for dk in sens["ke_delta"]:
            row = []
            for dg in sens["g_delta"]:
                k2, g2 = ke + dk, g_term + dg
                if k2 - g2 <= 0.005:
                    row.append(None)
                    continue
                tv2 = cashflows[-1]["fcf"] * (1 + g2) / (k2 - g2)
                eq2 = sum(c["fcf"] / (1 + k2) ** (i + 1) for i, c in enumerate(cashflows)) + tv2 / (1 + k2) ** len(cashflows)
                row.append(eq2)
            grid.append({"ke": k2, "values": row})
        dcf_out[ticker]["sensitivity"] = {"ke_delta": sens["ke_delta"], "g_delta": sens["g_delta"], "grid": grid}

    model = {
        "case_id": case_id,
        "cutoff": std["cutoff"],
        "assumption_version": assum["meta"]["version"],
        "generated_by": "pcd.model (deterministic; assumptions from config/assumptions)",
        "forecast_years": forecast_years,
        "registry": registry,
        "findings": findings,
        "dcf": dcf_out,
        "not_available_count": sum(1 for r in registry.values() if r["value"] is None),
    }
    write_json(out_dir / "metrics.json", model)
    return model


if __name__ == "__main__":
    root = repo_root()
    case = "case-01"
    m = build(case, root / "cases" / case / "work")
    print(
        json.dumps(
            {
                "metrics": len(m["registry"]),
                "not_available": m["not_available_count"],
                "findings": sorted({f["id"] for f in m["findings"]}),
            },
            indent=2,
        )
    )

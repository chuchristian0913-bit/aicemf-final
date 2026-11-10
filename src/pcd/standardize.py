"""Standardisation: turn a frozen EDGAR payload into comparable annual facts.

Three things happen here, and each one exists because it breaks real coverage work:

1. **Tag resolution.** Issuers drift between XBRL tags. Visa's `Revenues` stops in
   FY2020; Fiserv's `DepreciationDepletionAndAmortization` stops after FY2023;
   Global Payments' `PaymentsToAcquirePropertyPlantAndEquipment` stops after FY2011.
   A resolver with an explicit, documented priority is the difference between a
   coverage table and a table with three holes silently filled by an agent's memory.

2. **Point-in-time admission.** Only values from filings dated at or before the case
   cutoff enter the registry. Post-cutoff rows are *counted and rejected*, so the
   cutoff gate has something to prove rather than a tautology to assert.

3. **Restatement and drift logs.** When the same period carries two different values
   from two different filings, that is a finding, not an inconvenience to average away.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .util import append_jsonl, read_json, rel, repo_root, safe_div, write_json

# --------------------------------------------------------------------- tag policy

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "RevenuesNetOfInterestExpense",
    "Revenues",
    "SalesRevenueNet",
]
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]
DA_TAGS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "OtherDepreciationAndAmortization",
]
EQUITY_TAGS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]
SHARES_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "CommonStockSharesOutstanding",
]
EPS_TAGS = ["EarningsPerShareDiluted"]

SINGLE = {
    "operating_income": (["OperatingIncomeLoss"], "USD", True),
    # Mastercard tags annual NetIncomeLoss only in a later amendment, so a strict
    # point-in-time reader needs the fallbacks. Priority still starts with the
    # primary tag: a fallback must never outrank the issuer's main concept.
    "net_income": (
        ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
        "USD",
        True,
    ),
    "cfo": (["NetCashProvidedByUsedInOperatingActivities"], "USD", True),
    "tax_expense": (["IncomeTaxExpenseBenefit"], "USD", True),
    "cash": (["CashAndCashEquivalentsAtCarryingValue"], "USD", False),
}

FLOW_DAYS = (330, 400)  # a 52/53-week or calendar year, not a quarter

ACCESSION_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{accn}-index.htm"

# ------------------------------------------------------------------------ helpers


def _dur_days(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def flatten(payload: dict) -> list[dict]:
    """One row per reported value, values untouched."""
    rows: list[dict] = []
    for taxonomy, concepts in payload.get("facts", {}).items():
        for concept, body in concepts.items():
            for unit, arr in body.get("units", {}).items():
                for r in arr:
                    rows.append(
                        {
                            "concept": concept,
                            "taxonomy": taxonomy,
                            "unit": unit,
                            "start": r.get("start"),
                            "end": r.get("end"),
                            "val": r.get("val"),
                            "accn": r.get("accn"),
                            "form": r.get("form"),
                            "filed": r.get("filed"),
                            "fy": r.get("fy"),
                            "fp": r.get("fp"),
                            "dur": _dur_days(r.get("start"), r.get("end")),
                        }
                    )
    return rows


def _is_annual_10k(row: dict, cutoff: str, flow: bool) -> bool:
    if not str(row.get("form", "")).startswith("10-K"):
        return False
    if not row.get("filed") or row["filed"] > cutoff:
        return False
    if flow:
        d = row.get("dur")
        return d is not None and FLOW_DAYS[0] <= d <= FLOW_DAYS[1]
    return True


def fiscal_ends(rows: list[dict], years: Iterable[int], cutoff: str) -> dict[int, str]:
    """Fiscal-year end date per label year.

    Anchored on the *modal* annual period across all flow facts, not on a single
    concept. Anchoring on one concept looks tidy and then fails: Mastercard tags
    annual `NetIncomeLoss` only in a later amendment, which would erase the whole
    company from the panel.
    """
    out: dict[int, str] = {}
    for y in years:
        cands = [r for r in rows if _is_annual_10k(r, cutoff, True) and (r["end"] or "")[:4] == str(y)]
        if not cands:
            continue
        groups: dict[str, list[str]] = {}
        for r in cands:
            groups.setdefault(r["end"], []).append(r["filed"])
        # most-supported end date; ties broken by the most recently filed
        best = max(groups.items(), key=lambda kv: (len(kv[1]), max(kv[1])))
        out[y] = best[0]
    return out


def candidates(rows: list[dict], concept: str, end: str, cutoff: str, flow: bool) -> list[dict]:
    return [
        r
        for r in rows
        if r["concept"] == concept
        and r["end"] == end
        and _is_annual_10k(r, cutoff, flow)
    ]


def pick(rows: list[dict], concepts: list[str], end: str, cutoff: str, flow: bool) -> dict | None:
    """Highest-priority tag that has an admissible value, latest filing within it.

    Priority beats recency on purpose: a deprecated tag that happens to be filed
    later must not win over the tag the issuer currently uses.
    """
    for concept in concepts:
        cands = candidates(rows, concept, end, cutoff, flow)
        if not cands:
            continue
        best = max(cands, key=lambda r: (r["filed"], r["accn"] or ""))
        best = dict(best)
        best["_alternatives"] = [
            {
                "concept": c,
                "value": max(candidates(rows, c, end, cutoff, flow), key=lambda r: (r["filed"], r["accn"] or ""))["val"],
            }
            for c in concepts
            if candidates(rows, c, end, cutoff, flow)
        ]
        return best
    return None


def _source_id(ticker: str, row: dict) -> str:
    return f"{ticker}|{row['concept']}|{row['end']}|{row['accn']}"


def _source_record(ticker: str, cik: str, row: dict) -> dict:
    return {
        "source_id": _source_id(ticker, row),
        "ticker": ticker,
        "cik": cik,
        "concept": row["concept"],
        "taxonomy": row["taxonomy"],
        "unit": row["unit"],
        "start": row["start"],
        "end": row["end"],
        "value": row["val"],
        "accn": row["accn"],
        "form": row["form"],
        "filed": row["filed"],
        "url": ACCESSION_URL.format(
            cik=str(int(cik)), accn_nodash=(row["accn"] or "").replace("-", ""), accn=row["accn"]
        ),
        "provenance_class": "edgar_xbrl",
    }


def series_breaks(
    ticker: str, per_year: dict, restatements: list[dict], years: list[int]
) -> list[dict]:
    """Detect places where a time series stops being a like-for-like comparison.

    Two distinct causes, both of which a naive pipeline publishes without noticing:

    * **tag change** - the issuer switches XBRL concept, so the label is the same and
      the definition is not. Fiserv's D&A drops from $3.2bn to $1.7bn between FY2023
      and FY2024 purely because the tag changed.
    * **recast** - a later filing re-presents prior years on a new basis, typically
      after a divestiture. Global Payments' FY2023 revenue is $9.65bn as first filed
      and $7.38bn as re-presented in the FY2025 10-K. Both are true; they are not
      comparable, and only one of them is comparable with FY2025.
    """
    out: list[dict] = []
    metrics: set[str] = set()
    for d in per_year.values():
        metrics.update(d["tags"])

    for metric in sorted(metrics):
        series = [
            (f"FY{y}", per_year[f"FY{y}"]["tags"][metric], per_year[f"FY{y}"]["values"].get(metric))
            for y in years
            if f"FY{y}" in per_year and metric in per_year[f"FY{y}"]["tags"]
        ]
        for prev, cur in zip(series, series[1:]):
            if prev[1] != cur[1]:
                out.append(
                    {
                        "kind": "tag_change",
                        "ticker": ticker,
                        "metric": metric,
                        "from_fy": prev[0],
                        "from_tag": prev[1],
                        "to_fy": cur[0],
                        "to_tag": cur[1],
                        "from_value": prev[2],
                        "to_value": cur[2],
                        "reported_change": round((cur[2] - prev[2]) / abs(prev[2]), 6) if prev[2] else None,
                        "why": "issuer changed XBRL concept; the year-on-year change is a definition change",
                        "requires_note_disclosure": True,
                    }
                )

        recast = [r for r in restatements if r["ticker"] == ticker and r["metric"] == metric and (r["rel_diff"] or 0) > 0.02]
        if recast:
            affected = sorted({r["fy"] for r in recast})
            first = min(affected, key=lambda f: int(f[2:]))
            earlier = [f"FY{y}" for y in years if f"FY{y}" < first and f"FY{y}" in per_year]
            out.append(
                {
                    "kind": "recast",
                    "ticker": ticker,
                    "metric": metric,
                    "affected_fys": affected,
                    "comparison_basis_changes_before": first,
                    "not_comparable_fys": earlier,
                    "max_rel_revision": max(r["rel_diff"] or 0 for r in recast),
                    "why": "prior years re-presented in a later filing; values before the earliest "
                    "re-presented year are on a different basis",
                    "requires_note_disclosure": True,
                }
            )
    return out


# -------------------------------------------------------------------------- build


def standardize(case_id: str, case_dir: Path, out_dir: Path) -> dict:
    manifest = read_json(case_dir / "MANIFEST.json")
    if not manifest:
        raise FileNotFoundError(f"no frozen snapshot at {rel(case_dir)}")
    cutoff = manifest["cutoff"]
    years = manifest.get("scope", {}).get("years") or []

    run_cfg = _load_run_cfg()
    years = run_cfg.get("case", {}).get("fiscal_years", years)
    universe = run_cfg.get("case", {}).get("universe", manifest["scope"]["universe"])

    sources_path = out_dir / "sources.jsonl"
    if sources_path.exists():
        sources_path.unlink()

    tickers: dict[str, Any] = {}
    drift: list[dict] = []
    restatements: list[dict] = []
    series_all: list[dict] = []
    cutoff_evidence: dict[str, Any] = {}

    for ticker in universe:
        # Resolved against the *given* case directory, not against the repository, so
        # this function is a pure function of a directory. The leakage gate depends on
        # that: it re-runs this same code over a perturbed copy of the snapshot.
        payload_path = case_dir / "raw" / f"{ticker}.json"
        payload = read_json(payload_path)
        if payload is None:
            raise FileNotFoundError(f"missing frozen payload: {rel(payload_path)}")
        cik = manifest["tickers"][ticker]["cik"]
        rows = flatten(payload)

        ends = fiscal_ends(rows, years, cutoff)
        admissible = [r for r in rows if r.get("filed") and r["filed"] <= cutoff]
        rejected = [r for r in rows if r.get("filed") and r["filed"] > cutoff]
        cutoff_evidence[ticker] = {
            "rows_total": len(rows),
            "rows_admissible": len(admissible),
            "rows_rejected_after_cutoff": len(rejected),
            "max_filed_admitted": max((r["filed"] for r in admissible), default=None),
            "min_filed_rejected": min((r["filed"] for r in rejected), default=None),
            "distinct_rejected_accn": len({r["accn"] for r in rejected}),
        }

        per_year: dict[str, Any] = {}
        not_available: list[dict] = []

        for y in years:
            end = ends.get(y)
            if not end:
                not_available.append(
                    {
                        "metric": f"{ticker}.FY{y}.*",
                        "reason": "no annual income-statement fact admissible at cutoff",
                        "action": "escalate: verify filing exists before cutoff",
                    }
                )
                continue

            fy_key = f"FY{y}"
            values: dict[str, Any] = {}
            prov: dict[str, list[str]] = {}
            tags: dict[str, str] = {}

            def take(name: str, concepts: list[str], unit: str, flow: bool) -> None:
                row = pick(rows, concepts, end, cutoff, flow)
                if not row:
                    not_available.append(
                        {
                            "metric": f"{ticker}.{fy_key}.{name}",
                            "reason": f"no admissible fact for any of {concepts}",
                            "action": "render not_available; do not substitute zero",
                        }
                    )
                    return
                values[name] = row["val"]
                tags[name] = row["concept"]
                sid = _source_id(ticker, row)
                prov[name] = [sid]
                append_jsonl(sources_path, _source_record(ticker, cik, row))

                alts = [a for a in row.get("_alternatives", []) if a["concept"] != row["concept"]]
                if alts:
                    base = row["val"]
                    worst = max(alts, key=lambda a: abs((a["value"] - base) / base) if base else 0)
                    rel_diff = abs((worst["value"] - base) / base) if base else 0.0
                    if rel_diff > 0.005:
                        drift.append(
                            {
                                "ticker": ticker,
                                "fy": fy_key,
                                "metric": name,
                                "chosen_tag": row["concept"],
                                "chosen_value": base,
                                "alternatives": alts,
                                "rel_diff_vs_worst_alternative": round(rel_diff, 6),
                                "why": "competing tag reports a materially different value in the same filing; "
                                "chosen by documented priority, not by magnitude",
                                "requires_note_disclosure": True,
                            }
                        )

                # restatement: same concept/period, different value, both before cutoff
                all_rows = candidates(rows, row["concept"], end, cutoff, flow)
                distinct = {r["val"] for r in all_rows}
                if len(distinct) > 1:
                    first = min(all_rows, key=lambda r: r["filed"])
                    last = max(all_rows, key=lambda r: r["filed"])
                    restatements.append(
                        {
                            "ticker": ticker,
                            "fy": fy_key,
                            "metric": name,
                            "concept": row["concept"],
                            "first_filed": first["filed"],
                            "first_value": first["val"],
                            "last_filed": last["filed"],
                            "last_value": last["val"],
                            "rel_diff": round(abs(last["val"] - first["val"]) / abs(first["val"]), 6) if first["val"] else None,
                            "used": "latest filing at or before cutoff",
                        }
                    )

            take("revenue", REVENUE_TAGS, "USD", True)
            for name, (concepts, unit, flow) in SINGLE.items():
                take(name, concepts, unit, flow)
            take("capex", CAPEX_TAGS, "USD", True)
            take("da", DA_TAGS, "USD", True)
            take("equity", EQUITY_TAGS, "USD", False)
            take("shares_diluted", SHARES_TAGS, "shares", True)
            take("eps_diluted", EPS_TAGS, "USD/shares", True)

            per_year[fy_key] = {
                "fy_end": end,
                "values": values,
                "tags": tags,
                "provenance": prov,
            }

        fy_end_months = sorted({v["fy_end"][5:7] for v in per_year.values() if v.get("fy_end")})
        ticker_breaks = series_breaks(ticker, per_year, restatements, list(years))
        series_all.extend(ticker_breaks)
        tickers[ticker] = {
            "entity_name": manifest["tickers"][ticker]["entity_name"],
            "cik": cik,
            "fiscal_year_end_month": fy_end_months[0] if len(fy_end_months) == 1 else "mixed",
            "years": per_year,
            "not_available": not_available,
            "series_breaks": ticker_breaks,
        }

    out = {
        "case_id": case_id,
        "cutoff": cutoff,
        "years": list(years),
        "generated_by": "pcd.standardize (deterministic, no model in the loop)",
        "tag_policy": {
            "revenue": REVENUE_TAGS,
            "capex": CAPEX_TAGS,
            "da": DA_TAGS,
            "equity": EQUITY_TAGS,
            "shares": SHARES_TAGS,
            "eps": EPS_TAGS,
        },
        "tickers": tickers,
        "drift": drift,
        "restatements": restatements,
        "series_breaks": series_all,
        "cutoff_evidence": cutoff_evidence,
    }

    write_json(out_dir / "standardized.json", out)
    return out


def _load_run_cfg() -> dict:
    import tomllib

    with open(repo_root() / "config" / "run.toml", "rb") as fh:
        return tomllib.load(fh)


def derived_metrics(values: dict[str, float], prior_revenue: float | None) -> dict[str, float | None]:
    """Ratios from unrounded inputs. None means 'not available', never 0."""
    rev = values.get("revenue")
    fcf = None
    if values.get("cfo") is not None and values.get("capex") is not None:
        fcf = values["cfo"] - values["capex"]
    ebitda = None
    if values.get("operating_income") is not None and values.get("da") is not None:
        ebitda = values["operating_income"] + values["da"]
    return {
        "ebit_margin": safe_div(values.get("operating_income"), rev),
        "net_margin": safe_div(values.get("net_income"), rev),
        "fcf": fcf,
        "fcf_conversion": safe_div(fcf, values.get("net_income")),
        "ebitda_proxy": ebitda,
        "revenue_growth": (rev / prior_revenue - 1) if (rev and prior_revenue) else None,
        "capex_intensity": safe_div(values.get("capex"), rev),
        "da_intensity": safe_div(values.get("da"), rev),
    }


if __name__ == "__main__":
    root = repo_root()
    case = "case-01"
    out = standardize(case, root / "data" / "frozen" / case, root / "cases" / case / "work")
    print(json.dumps({"tickers": list(out["tickers"]), "drift": len(out["drift"]), "restatements": len(out["restatements"])}, indent=2))

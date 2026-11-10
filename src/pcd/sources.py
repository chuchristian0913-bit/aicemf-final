"""Source adapter: freeze a dated EDGAR snapshot.

This is the only module in the project permitted to open a network socket, and it
runs exactly once per case. Everything downstream reads the frozen bytes, so a
recomputation a year later produces the same numbers even if the issuer restates.

Design note, section 2.2: the freeze is a *checkpoint*, not a download. What gets
written is (a) the payload, (b) its hash, (c) the query URL, and (d) the statement
of what was deliberately kept out of scope.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .util import (
    canon,
    ensure_dir,
    now_iso,
    read_json,
    rel,
    repo_root,
    sha256_bytes,
    write_json,
)

# --------------------------------------------------------------------------- inputs

# Payments coverage universe. CIKs are permanent identifiers; tickers are not.
UNIVERSE: dict[str, dict[str, str]] = {
    "V": {"cik": "0001403161", "name": "Visa Inc."},
    "MA": {"cik": "0001141391", "name": "Mastercard Incorporated"},
    "PYPL": {"cik": "0001633917", "name": "PayPal Holdings, Inc."},
    "FI": {"cik": "0000798354", "name": "Fiserv, Inc."},
    "GPN": {"cik": "0001123360", "name": "Global Payments Inc."},
}

# Concept whitelist. Kept deliberately small: a coverage model needs a dozen numbers,
# not four hundred, and every extra concept is another way to pick the wrong tag.
#
# The whitelist contains *alternatives* on purpose. XBRL tag drift is not an edge
# case: in this very universe Visa stopped tagging "Revenues" in FY2020, Fiserv
# stopped tagging "DepreciationDepletionAndAmortization" after FY2023, and Global
# Payments stopped tagging "PaymentsToAcquirePropertyPlantAndEquipment" after FY2011.
# A single-tag reader silently returns stale or missing numbers for three of five
# names. See standardize.py for the priority order and the drift log it produces.
CONCEPTS: dict[str, str] = {
    # --- revenue candidates, highest priority first
    "RevenueFromContractWithCustomerExcludingAssessedTax": "USD",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "USD",
    "RevenuesNetOfInterestExpense": "USD",
    "Revenues": "USD",
    "SalesRevenueNet": "USD",
    # --- income statement
    "OperatingIncomeLoss": "USD",
    "NetIncomeLoss": "USD",
    # Mastercard tags consolidated net income as ProfitLoss in its 10-Ks and only
    # files a NetIncomeLoss fact in a later amendment. Without this alternative the
    # panel loses one of five names entirely.
    "ProfitLoss": "USD",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "USD",
    "IncomeTaxExpenseBenefit": "USD",
    "EarningsPerShareDiluted": "USD/shares",
    # --- cash flow
    "NetCashProvidedByUsedInOperatingActivities": "USD",
    "PaymentsToAcquirePropertyPlantAndEquipment": "USD",
    "PaymentsToAcquireProductiveAssets": "USD",
    "DepreciationDepletionAndAmortization": "USD",
    "DepreciationAndAmortization": "USD",
    "DepreciationAmortizationAndAccretionNet": "USD",
    "OtherDepreciationAndAmortization": "USD",
    # --- balance sheet
    "StockholdersEquity": "USD",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "USD",
    "CashAndCashEquivalentsAtCarryingValue": "USD",
    # --- share count
    "WeightedAverageNumberOfDilutedSharesOutstanding": "shares",
    "CommonStockSharesOutstanding": "shares",
}

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC fair-access policy asks for a descriptive User-Agent. No credential is stored:
# the string below is a contact description, not a secret.
USER_AGENT = "FISF-Fudan AI-Coding-in-Finance student research chuchristian0913@gmail.com"

FILING_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "6-K")


# --------------------------------------------------------------------------- fetch


class FetchError(RuntimeError):
    pass


def _get(url: str, attempts: int = 3, backoff: tuple[int, ...] = (2, 8)) -> tuple[bytes, int]:
    """GET with a bounded retry on transient failures only.

    A 4xx is not retried: retrying a bad request burns budget and hides the bug.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw, resp.status
        except urllib.error.HTTPError as exc:
            last = exc
            if 400 <= exc.code < 500:
                raise FetchError(f"{url} -> HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if i < attempts - 1:
            time.sleep(backoff[min(i, len(backoff) - 1)])
    raise FetchError(f"{url} failed after {attempts} attempts: {last}")


def _filter(payload: dict, tickers_concepts: dict[str, str]) -> dict:
    """Keep the whitelisted concepts, keep *every* reported value for them.

    Post-cutoff values are deliberately retained here. If the freeze dropped them,
    the cutoff gate downstream would pass trivially and test nothing.
    """
    facts = payload.get("facts", {})
    out: dict[str, Any] = {"cik": payload.get("cik"), "entityName": payload.get("entityName"), "facts": {}}
    for taxonomy in ("us-gaap", "dei"):
        src = facts.get(taxonomy, {})
        kept = {k: v for k, v in src.items() if k in tickers_concepts}
        if kept:
            out["facts"][taxonomy] = kept
    return out


# --------------------------------------------------------------------------- freeze


def freeze(case_id: str, case_dir: Path, cutoff: str, as_of_note: str | None = None) -> dict:
    """Fetch and persist one immutable snapshot for `case_id`."""
    raw_dir = ensure_dir(case_dir / "raw")
    manifest_path = case_dir / "MANIFEST.json"

    if manifest_path.exists():
        # Idempotent: re-running freeze on an existing case must not silently
        # replace evidence. A new snapshot means a new case id.
        raise FileExistsError(
            f"{rel(manifest_path)} already exists. A new snapshot is a new case id, "
            "never an overwrite of frozen evidence."
        )

    entries: dict[str, dict] = {}
    filings: list[dict] = []
    total_full = 0
    total_stored = 0

    for ticker, meta in UNIVERSE.items():
        url = COMPANYFACTS_URL.format(cik=meta["cik"])
        raw, status = _get(url)
        full_sha = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        trimmed = _filter(payload, CONCEPTS)

        stored = (canon(trimmed) + "\n").encode("utf-8")
        path = raw_dir / f"{ticker}.json"
        path.write_bytes(stored)

        total_full += len(raw)
        total_stored += len(stored)
        entries[ticker] = {
            "cik": meta["cik"],
            "entity_name": payload.get("entityName", meta["name"]),
            "url": url,
            "http_status": status,
            "retrieved_at": now_iso(),
            "full_payload_sha256": full_sha,
            "full_payload_bytes": len(raw),
            "stored_path": rel(path),
            "stored_sha256": sha256_bytes(stored),
            "stored_bytes": len(stored),
            "concepts_kept": sorted(
                c for tax in trimmed["facts"].values() for c in tax.keys()
            ),
        }

        # Filing index: the dates that make the cutoff meaningful.
        seen: set[tuple] = set()
        for tax in trimmed["facts"].values():
            for concept, body in tax.items():
                for unit, arr in body.get("units", {}).items():
                    for row in arr:
                        if row.get("form") not in FILING_FORMS:
                            continue
                        key = (row.get("accn"), row.get("form"), row.get("filed"), row.get("end"))
                        if key in seen:
                            continue
                        seen.add(key)
                        filings.append(
                            {
                                "ticker": ticker,
                                "accn": row.get("accn"),
                                "form": row.get("form"),
                                "filed": row.get("filed"),
                                "period_end": row.get("end"),
                                "fy": row.get("fy"),
                                "fp": row.get("fp"),
                            }
                        )

    filings.sort(key=lambda r: (r["ticker"], r["filed"] or "", r["accn"] or ""))

    manifest = {
        "case_id": case_id,
        "cutoff": cutoff,
        "cutoff_basis": as_of_note
        or "Information cutoff fixed before the run: no source filed after this date is admissible.",
        "frozen_at": now_iso(),
        "tool": f"pcd {__version__}",
        "source": {
            "name": "SEC EDGAR XBRL company facts API",
            "base_url": "https://data.sec.gov/api/xbrl/companyfacts/",
            "user_agent": USER_AGENT,
            "licence": "Public domain US government filings.",
            "authentication": "none required; no credential is stored in this repository",
        },
        "scope": {
            "universe": sorted(UNIVERSE),
            "concepts": sorted(CONCEPTS),
            "included": "10-K / 10-Q / amendments, all periods, values as reported",
            "excluded": [
                "market prices (no licensed feed in this project - see design note 2.6)",
                "segment and KPI disclosures (XBRL dimensional facts)",
                "transcripts, guidance, consensus estimates",
                "8-K / earnings releases",
                "anything filed after the cutoff",
            ],
        },
        "tickers": entries,
        "integrity": {
            "full_payload_bytes": total_full,
            "stored_bytes": total_stored,
            "compression_note": "payloads trimmed to the concept whitelist, values unmodified",
        },
    }

    write_json(manifest_path, manifest)
    write_json(case_dir / "filings.json", filings)
    return manifest


def latest_filing(ticker: str, filings: list[dict], cutoff: str, form_prefix: str = "10-K") -> dict | None:
    """Most recent filing of a form, at or before cutoff. Used for the trigger."""
    rows = [
        r
        for r in filings
        if r["ticker"] == ticker
        and r["form"].startswith(form_prefix)
        and r["filed"]
        and r["filed"] <= cutoff
    ]
    return max(rows, key=lambda r: r["filed"]) if rows else None


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="pcd.sources", description="Freeze a dated EDGAR snapshot.")
    ap.add_argument("command", choices=["freeze", "inspect"])
    ap.add_argument("--case", default="case-01")
    ap.add_argument("--cutoff", default=None)
    args = ap.parse_args(argv)

    case_dir = repo_root() / "data" / "frozen" / args.case

    if args.command == "freeze":
        manifest = read_json(case_dir / "MANIFEST.json")
        cutoff = args.cutoff or (manifest or {}).get("cutoff")
        if not cutoff:
            ap.error("--cutoff is required for a new case")
        m = freeze(args.case, case_dir, cutoff)
        print(f"frozen {args.case}: {len(m['tickers'])} tickers, cutoff {cutoff}")
        for t, e in m["tickers"].items():
            print(f"  {t:5s} {e['concepts_kept'].__len__():2d} concepts  {e['stored_bytes']:>7,d} B  {e['stored_sha256'][:12]}")
        return 0

    manifest = read_json(case_dir / "MANIFEST.json")
    if not manifest:
        print("no snapshot for that case")
        return 1
    filings = read_json(case_dir / "filings.json", [])
    print(json.dumps(manifest["scope"], indent=2))
    for t in manifest["scope"]["universe"]:
        f = latest_filing(t, filings, manifest["cutoff"])
        print(f"{t:5s} latest 10-K on/before {manifest['cutoff']}: {f['filed'] if f else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


def verify_snapshot(case_dir: Path) -> dict:
    """Re-hash the frozen payloads against the manifest.

    Called before a run resumes. A mismatch means the evidence changed outside the
    controller, which is a stop condition, not a warning.
    """
    manifest = read_json(case_dir / "MANIFEST.json")
    if not manifest:
        raise FileNotFoundError(f"no manifest at {rel(case_dir)}")
    bad = []
    for ticker, entry in manifest["tickers"].items():
        path = case_dir / "raw" / f"{ticker}.json"
        if not path.exists():
            bad.append({"ticker": ticker, "reason": "payload missing"})
            continue
        actual = sha256_bytes(path.read_bytes())
        if actual != entry["stored_sha256"]:
            bad.append({"ticker": ticker, "reason": "hash mismatch", "expected": entry["stored_sha256"][:16], "actual": actual[:16]})
    return {"case_id": manifest["case_id"], "cutoff": manifest["cutoff"], "ok": not bad, "problems": bad}

"""Author the round-001 claims file from the verified registry.

This is the generator role's artifact. It is built here rather than typed by hand so
that every `quoted_value` is the exact number the model produced: a transcription
slip in a 50-claim note is not an error the critic should have to find.

Version 2 of this script. Version 1 (archived as round-001-attempt1) mixed Visa's
September year-end with four December year-ends and said nothing about it; gate G11
rejected the submission and the carried finding required a `period_alignment`
limitation, a footnote reference in the comparative prose, and a footnotes block
entry. Those three edits are the only difference from version 1.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[5]  # note -> round-001 -> rounds -> case-01 -> cases -> repo
REG = json.loads((ROOT / "cases/case-01/work/metrics.json").read_text())["registry"]

SPEC = [
    ("c01", "V.FY2025.revenue", "usd_b"),
    ("c02", "V.FY2025.revenue_growth", "pct1"),
    ("c03", "V.FY2025.ebit_margin", "pct1"),
    ("c04", "V.FY2025.net_margin", "pct1"),
    ("c05", "V.FY2025.fcf_conversion", "pct1"),
    ("c06", "MA.FY2025.revenue", "usd_b"),
    ("c07", "MA.FY2025.revenue_growth", "pct1"),
    ("c08", "MA.FY2025.ebit_margin", "pct1"),
    ("c09", "MA.FY2025.net_margin", "pct1"),
    ("c10", "MA.FY2025.fcf_conversion", "pct1"),
    ("c11", "PYPL.FY2025.revenue", "usd_b"),
    ("c12", "PYPL.FY2025.revenue_growth", "pct1"),
    ("c13", "PYPL.FY2025.ebit_margin", "pct1"),
    ("c14", "PYPL.FY2025.net_margin", "pct1"),
    ("c15", "PYPL.FY2025.fcf_conversion", "pct1"),
    ("c16", "FI.FY2025.revenue", "usd_b"),
    ("c17", "FI.FY2025.revenue_growth", "pct1"),
    ("c18", "FI.FY2025.ebit_margin", "pct1"),
    ("c19", "FI.FY2025.net_margin", "pct1"),
    ("c20", "FI.FY2025.fcf_conversion", "pct1"),
    ("c21", "GPN.FY2025.revenue", "usd_b"),
    ("c22", "GPN.FY2025.revenue_growth", "pct1"),
    ("c23", "GPN.FY2025.ebit_margin", "pct1"),
    ("c24", "GPN.FY2025.net_margin", "pct1"),
    ("c25", "GPN.FY2025.fcf_conversion", "pct1"),
    ("c26", "V.FY2022-FY2025.revenue_cagr", "pct1"),
    ("c27", "MA.FY2022-FY2025.revenue_cagr", "pct1"),
    ("c28", "PYPL.FY2022-FY2025.revenue_cagr", "pct1"),
    ("c29", "FI.FY2022-FY2025.revenue_cagr", "pct1"),
    ("c30", "GPN.FY2023-FY2025.revenue_cagr", "pct1"),
    ("c31", "V.FY2025.fcf", "usd_b"),
    ("c32", "MA.FY2025.fcf", "usd_b"),
    ("c33", "V.DCF.equity_value", "usd_b"),
    ("c34", "MA.DCF.equity_value", "usd_b"),
    ("c35", "PYPL.DCF.equity_value", "usd_b"),
    ("c36", "FI.DCF.equity_value", "usd_b"),
    ("c37", "GPN.DCF.equity_value", "usd_b"),
    ("c38", "MA.DCF.per_share", "usd_ps"),
    ("c39", "PYPL.DCF.per_share", "usd_ps"),
    ("c40", "FI.DCF.per_share", "usd_ps"),
    ("c41", "GPN.DCF.per_share", "usd_ps"),
    ("c42", "V.DCF.ke", "pct1"),
    ("c43", "MA.DCF.ke", "pct1"),
    ("c44", "PYPL.DCF.ke", "pct1"),
    ("c45", "V.DCF.terminal_share", "pct1"),
    ("c46", "PYPL.DCF.terminal_share", "pct1"),
    ("c47", "GPN.DCF.terminal_share", "pct1"),
    ("c48", "MA.DCF.terminal_share", "pct1"),
    ("c49", "FI.DCF.terminal_share", "pct1"),
    ("c50", "PCD.ASSUMP.terminal_growth", "pct1"),
]

claims = []
for cid, mid, fmt in SPEC:
    rec = REG[mid]
    assert rec["value"] is not None, mid
    claims.append({"claim_id": cid, "metric_id": mid, "quoted_value": rec["value"], "fmt": fmt,
                   "note": f"{rec['ticker']} {rec['period']} {rec['name']}"})

note = {
    "candidate_id": "cand-a1b",
    "title": "Payments coverage: FY2025 annual update",
    "standfirst": "Mastercard grew fastest and Visa turned the most revenue into cash; the two networks again out-earned the processors on every margin line.",
    "claims": claims,
    "blocks": [
        {"kind": "heading", "text": "What we compared"},
        {"kind": "para", "text": "We standardised the annual filings of five listed payments businesses from SEC EDGAR XBRL, using each issuer's latest annual report filed on or before the information cutoff. Revenue, operating income, net income, operating cash flow and capital expenditure are taken as reported in the filings; every ratio below is computed from unrounded figures rather than from the rounded numbers printed here. The fiscal year-ends do not align.[[period_alignment]]"},
        {"kind": "table", "spec": "lrrrrr",
         "caption": "Latest reported fiscal year. Revenue in $bn; growth is year on year; margins and conversion are ratios of reported values.",
         "columns": ["", "Revenue ($bn)", "Growth", "Operating margin", "Net margin", "FCF conversion"],
         "rows": [
             {"label": "Visa (Sep-25)", "cells": ["{c01}", "{c02}", "{c03}", "{c04}", "{c05}"]},
             {"label": "Mastercard (Dec-25)", "cells": ["{c06}", "{c07}", "{c08}", "{c09}", "{c10}"]},
             {"label": "PayPal (Dec-25)", "cells": ["{c11}", "{c12}", "{c13}", "{c14}", "{c15}"]},
             {"label": "Fiserv (Dec-25)", "cells": ["{c16}", "{c17}", "{c18}", "{c19}", "{c20}"]},
             {"label": "Global Payments (Dec-25)", "cells": ["{c21}", "{c22}", "{c23}", "{c24}", "{c25}"]},
         ]},
        {"kind": "heading", "text": "Growth"},
        {"kind": "para", "text": "Mastercard grew revenue {c07} to ${c06}bn, the fastest in the group, and its three-year compound rate of {c27} is the only one above ten percent. Visa added {c02} to ${c01}bn, a three-year compound rate of {c26}. PayPal and Fiserv grew {c12} and {c17} respectively, with compound rates of {c28} and {c29}. Global Payments reported {c22} at ${c21}bn; that figure is on a post-divestiture basis, so the two-year compound rate of {c30} is the only growth measure comparable with the business as it now stands."},
        {"kind": "heading", "text": "Profitability and cash generation"},
        {"kind": "para", "text": "The networks remain a structurally different business from the processors. Visa earned an operating margin of {c03} and a net margin of {c04}, Mastercard {c08} and {c09}. Global Payments ({c23}) and Fiserv ({c18}) sit in the twenties and PayPal is lowest at {c13}. Cash conversion is uniformly strong: every company converted more than its reported net income into free cash flow in the year, from {c15} at PayPal to {c25} at Global Payments, with Visa generating ${c31}bn and Mastercard ${c32}bn of free cash flow."},
        {"kind": "heading", "text": "Valuation"},
        {"kind": "para", "text": "Our equity DCF discounts three years of free cash flow at a cost of equity between {c42} and {c44}, and assumes terminal growth of {c50}. On that basis Visa is worth ${c33}bn of equity and Mastercard ${c34}bn; per share, Mastercard ${c38}, Fiserv ${c40}, Global Payments ${c41} and PayPal ${c39}. Visa's per-share value is withheld: the company does not tag a diluted share count in its XBRL filings, and we do not estimate an input we cannot source."},
        {"kind": "table", "spec": "lrrrrr",
         "caption": "Equity DCF at the assumption file dated with this case. Per-share values use reported diluted weighted-average shares; the terminal share is the fraction of value coming from the terminal assumption.",
         "columns": ["", "Equity value ($bn)", "Per share ($)", "Cost of equity", "Terminal growth", "Terminal share"],
         "rows": [
             {"label": "Visa", "cells": ["{c33}", "n/a", "{c42}", "{c50}", "{c45}"]},
             {"label": "Mastercard", "cells": ["{c34}", "{c38}", "{c43}", "{c50}", "{c48}"]},
             {"label": "PayPal", "cells": ["{c35}", "{c39}", "{c44}", "{c50}", "{c46}"]},
             {"label": "Fiserv", "cells": ["{c36}", "{c40}", "{c43}", "{c50}", "{c49}"]},
             {"label": "Global Payments", "cells": ["{c37}", "{c41}", "{c43}", "{c50}", "{c47}"]},
         ]},
        {"kind": "para", "text": "Between {c46} and {c45} of each valuation comes from the terminal assumption. These are therefore consistency checks on the assumptions rather than price targets, and no market price is used anywhere in this note."},
        {"kind": "footnotes", "keys": ["period_alignment", "tag_change:FI:da", "recast:GPN:revenue", "recast:GPN:operating_income",
                                       "recast:GPN:cfo", "recast:GPN:da", "recast:GPN:tax_expense", "shares_V"]},
        {"kind": "sources"},
        {"kind": "footer_note", "text": "AI use and approval. The draft was produced by the PCD generator role from the verified metric registry; the figures were recomputed independently by the critic role; publication requires a recorded approval from the analyst of record. Internal research exercise, not investment advice."},
    ],
    "limitations": [
        {"key": "period_alignment", "text": "Visa's fiscal year ends in September; the other four end in December. The comparison is between each company's latest annual report, not a common calendar year, and Visa's figures therefore cover a period ending roughly nine months earlier than the December year-ends."},
        {"key": "tag_change:FI:da", "text": "Fiserv changed the XBRL tag behind depreciation and amortisation between FY2023 and FY2024, from DepreciationDepletionAndAmortization to OtherDepreciationAndAmortization. The reported series falls from roughly $3.2bn to $1.7bn with no economic event behind it, so EBITDA-based measures are not used anywhere in this note."},
        {"key": "recast:GPN:revenue", "text": "Global Payments re-presented FY2023 and FY2024 after its divestitures: FY2023 revenue is $9.65bn as first filed and $7.38bn as restated in the FY2025 annual report. Growth rates spanning the recast are not like-for-like, so only the FY2023-FY2025 window is used."},
        {"key": "recast:GPN:operating_income", "text": "Operating income for FY2023 and FY2024 was re-presented on the same post-divestiture basis as revenue; the same window restriction applies to any margin comparison."},
        {"key": "recast:GPN:cfo", "text": "Cash flow from operations for FY2023 and FY2024 was re-presented following the divestitures; the FY2022 value remains on the pre-divestiture basis."},
        {"key": "recast:GPN:da", "text": "Depreciation and amortisation for FY2023 and FY2024 was re-presented following the divestitures."},
        {"key": "recast:GPN:tax_expense", "text": "Tax expense for FY2023 and FY2024 was re-presented following the divestitures."},
        {"key": "shares_V", "text": "Visa does not tag a diluted share count or diluted EPS in XBRL at or before the cutoff. Its per-share intrinsic value is withheld rather than estimated; an attested cover-page share count would be required to publish one."},
    ],
    "footnote_refs": ["period_alignment"],
}

out = ROOT / "cases/case-01/rounds/round-001/note/claims.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(note, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print("wrote", out, len(claims), "claims")

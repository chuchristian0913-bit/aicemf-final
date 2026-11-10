---
name: edgar-snapshot-freeze
description: Freeze a point-in-time SEC EDGAR XBRL snapshot with integrity hashes and a cutoff filter that is actually exercised.
when_to_use: Starting any research case whose numbers must be defensible against "where did this come from" and "is this the data as of the decision date".
---

# Freeze an EDGAR XBRL snapshot

1. **Collect CIKs first.** `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany` or the
   `company_tickers.json` mapping. A ticker→CIK table lives in `src/pcd/sources.py::UNIVERSE`.
2. **Fetch companyfacts** `https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit-cik}.json`
   with a descriptive User-Agent (`Sample Company research contact@email` — EDGAR blocks
   generic agents). Responses may be gzip-compressed: check `Content-Encoding` and
   `gzip.decompress` before parsing, or you get `UnicodeDecodeError: 0x8b`.
3. **Trim to a concept whitelist** before storing; values unmodified. Expect tag drift:
   issuers switch concepts mid-series (`Revenues` ↔ `RevenueFromContractWithCustomer…`,
   `DepreciationDepletionAndAmortization` ↔ `OtherDepreciationAndAmortization`,
   `NetIncomeLoss` vs `ProfitLoss` for Mastercard). Whitelist the alternatives deliberately.
4. **Keep post-cutoff rows in the raw payload.** The cutoff filter belongs in
   standardisation, and gate G04 must see rows it actually rejects — a filter that
   rejected nothing proves nothing.
5. **Write MANIFEST.json** with per-ticker `stored_sha256`, `stored_path`, `retrieved_at`,
   `full_payload_sha256`, and the cutoff + its basis. The manifest makes the snapshot
   verifiable and, once written, immutable: a new snapshot is a NEW case id, never an
   overwrite (`freeze()` raises `FileExistsError`).
6. **Verify at adoption.** After any restart, re-hash every stored payload against the
   manifest before the controller adopts it (`sources.verify_snapshot`).

## Pitfalls learned on live data
- MA tags consolidated net income as `ProfitLoss`; anchoring fiscal calendars on
  `NetIncomeLoss` alone yields zero admissible years. Anchor on the modal annual period
  across ALL flow facts.
- FI switched D&A tags mid-series (−47% with no economic event). Single-tag readers
  publish phantom collapses.
- Stooq/Yahoo/other free price APIs are unreliable from CN networks; the whole desk runs
  on EDGAR alone and marks market prices `not_available`.

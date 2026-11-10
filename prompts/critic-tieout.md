# pcd-critic-tieout

Verify the candidate's numbers independently. You are not a proofreader; you
re-derive.

## Do
- Recompute FY2025 `ebit_margin`, `net_margin`, `fcf_conversion` and
  `revenue_growth` for every ticker from `work/standardized.json` source values —
  never from `work/metrics.json` ratios (that would test the model against
  itself).
- Check every claim's `quoted_value` against the registry; any drift means the
  claims file was edited after generation.
- Check CAGR windows: no cited CAGR may span a disclosed recast break, and every
  cited CAGR must have `comparability = "comparable"`.

## Write
`cases/<case>/rounds/<round>/critic/tieout.json` with checked items, evidence,
and findings (`severity: high` for any mismatch). An empty findings list is a
claim you are willing to defend line by line.

## Cannot
- edit anything under `note/`;
- read another critic's report — each critic reviews fresh;
- pass the candidate; only the controller routes.

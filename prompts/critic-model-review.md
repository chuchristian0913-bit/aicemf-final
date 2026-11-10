# pcd-critic-model-review

Rebuild the valuation with your own arithmetic and compare.

## Do
- Re-derive `ke = risk_free + beta × ERP`, the forecast, terminal value and
  equity value for every ticker directly from `config/assumptions/<case>.toml`.
- Check per-share values: present exactly when the share input exists, consistent
  with the recorded share provenance; withheld (`n/a`) otherwise, with the
  withholding documented by a finding.
- Check the sensitivity grid is monotone in growth (up) and in cost of equity
  (down) at every grid point, and terminal share ∈ (0, 1).

## Write
`cases/<case>/rounds/<round>/critic/model_review.json`.

## Cannot
- adjust assumptions to make the number look better;
- publish or approve; acceptance is the controller's routing, not yours.

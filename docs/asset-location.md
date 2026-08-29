# After-tax asset location

Asset Location is a deterministic planning surface for comparing a desired exposure across Resident Indian, NRO Non-PIS, NRE PIS, GIFT IBU, U.S., and global accounts. It never moves assets, stages orders, files an ITR, or assumes that a family transfer is tax-free.

## Evidence contract

Every tax rule exposes jurisdiction, applicability, legal reference, source URL, effective dates, last review date, required inputs, method, confidence, and whether CA review is required. Product/account calculations also require dated tax-rate evidence. Missing domicile, treaty, share-class, residency, or lot facts return `UNKNOWN` or `TAX_REVIEW_REQUIRED`; they never become a zero tax rate.

The comparison subtracts evidenced capital-gains tax, dividend/interest withholding, fund-level drag, TER/tracking difference, FX/conversion, brokerage/settlement, and exit load from bear/base/bull pre-tax scenarios. U.S.-situs estate review is a separate warning.

## Recommendation policy

The optimizer validates permitted instruments, ownership, repatriability, GIFT product identity, and evidence completeness. Its outputs are deliberately limited to:

- keep the current location;
- direct new contributions to another eligible account;
- migrate only after tax review;
- do not move when exit/transfer cost exceeds the evidenced benefit.

It never constructs an internal family transfer. Resident harvesting requires FIFO lots and an independent investment sell case. NRI withholding remains distinct from final tax liability and settlement/repatriation review.

## CA package

`GET /api/portfolio/tax/ca-package.xlsx` downloads rules and sources, assumptions, FIFO lots, and proposed actions. The workbook is a review packet, not a tax return.

Run the focused regression gate with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_asset_location.py
```

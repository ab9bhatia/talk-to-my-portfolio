# Design QA — setup labels, target price, and Growth maturity

Date: 2026-08-28

## Evidence

- Dashboard reference: `/var/folders/5f/7_v7_1gd0pv6l_ctkbcqd07c0000gp/T/codex-clipboard-0d457abf-d779-48a7-8561-7859768efce2.png`
- Dashboard implementation: `/private/tmp/ttmp-target-growth/dashboard-after.png`
- Growth reference: `/var/folders/5f/7_v7_1gd0pv6l_ctkbcqd07c0000gp/T/codex-clipboard-5e7394d3-4330-4e33-b0e4-2bf6f8e69569.png`
- Growth implementation: `/private/tmp/ttmp-target-growth/growth-after.png`
- Reference captures were supplied at 2870 × 912 and 2928 × 1694. The in-app browser is fixed at 1280 × 720, so this is a component/state comparison rather than a pixel-diff at the original viewport.

## Findings and resolution

| Priority | Finding | Resolution | Verification |
|---|---|---|---|
| P1 | Long setup labels crossed from Symbol into Account. | The pill is now capped to the Symbol cell, its label ellipsizes, and the move is compact (`−15.2%` / `+31.5%`) while the full wording remains in the tooltip. | CSS containment contract plus desktop table inspection. The current browser session had no cached actionable patterns, so live pattern text was unavailable for a screenshot. |
| P1 | `TGT%` lacked the corresponding analyst price. | Target price now appears on a second line in brackets, in native quote currency. | Live ADANIPORTS row rendered `106.9% (₹2,037)` without column overlap. |
| P1 | A one-day Growth chart implied analysis from a single point and left a large empty plot. | The one-point chart is replaced by a baseline guide; invalid comparison sections stay hidden until two closes exist. | One-day live state shows `Trend unlock`, `Useful baseline`, and the guided baseline panel. |
| P2 | Growth looked like a daily/intraday dashboard. | Hero and CTA now establish an after-market-close recording habit and weekly review cadence. | Copy and navigation verified in the live page. |

## Checks

- No page-level horizontal overflow was introduced at 1280 × 720.
- Holdings search, Growth range selector, Dashboard navigation, and Record market close remain operable.
- Empty/one-day Growth state is understandable without relying on chart color.
- Full setup semantics remain available in the pill tooltip after visual compaction.

final result: passed

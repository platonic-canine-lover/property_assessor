# Review log

Each commit after the first is a response to an independent code review of
the previous state. The reviewer ran the tests but not the linters; every
commit below was verified locally (ruff, mypy strict, pytest) before it was
pushed.

## Commit 1: first build

Lender criteria engine, purchase costs with SDLT, long-let and short-let
year, four returns, Cornwall rules, two-listing worked example. 25 tests.

## Review 1 → commit 2

| Finding | Fix |
| --- | --- |
| The business-rates switch fired on *projected* let nights, but the rule has three conditions and two of them are historical facts about the property. The model removed thousands of pounds of council tax on a condition it had not verified. | `rates.py` evaluates the three conditions separately, each met / not met / unknown with its figure and basis. Year 1 uses the candidate's evidenced letting history (none for a typical purchase, so the premium applies); a steady-state scenario shows the regime once a year at the projected occupancy is on record. The regime is an explicit input to the year calculation, never inferred from the projection. |
| Tax used a flat marginal rate; rental profit can push the applicant into a higher band. | Progressive 2026/27 bands with the allowance taper, profit stacked on earned income; finance-cost credit capped by interest, profit and tax due. Tests cover the band crossing and the Section 24 case where a cash loss still owes tax. The report warns when the profit pushes a basic-rate applicant over the threshold, since the lender's cover test changes too. |
| Break-even search across the council-tax cliff was not guaranteed sound. | Break-even is computed per regime with the regime fixed; a test checks cash flow is monotone in occupancy within each regime and that the solution brackets zero. |
| Linters not run in the reviewer's environment. | Run here; `ruff check`, `ruff format --check` and `mypy --strict` are in CI. |

Effect on the worked example: the St Eval short let went from +4.7% to
-0.3% year-1 cash-on-cash (doubled council tax and higher-rate tax), with
+2.0% in steady state. The earlier figure was flattering and wrong.

## Commit 3: comparable sales

A second review (an AI-generated architecture proposal) suggested a full
automated valuation model: gradient boosting, SHAP explanations, walkability
and school scores, risk and investment indices. One piece was taken: a
weighted comparable-sales engine over the Price Paid data already in the
snapshot, with the weights stated and the top comparables printed. The
rest was declined on the grounds this repository has been reviewed on:
there is no training data of that kind for Cornwall in hand, and the
scores would be exactly the unsourced numbers the first reviewer marked
provenance 9/10 for not producing.

## Review 2 → commit 4

| Finding | Fix |
| --- | --- |
| Company tax path was a flat 25% with no marginal relief or extraction tax; its cash-on-cash could be mistaken for a personal return | Individuals only: `Applicant` rejects `limited_company: true` with the reason. No company path exists to be wrong. |
| An unknown council tax band produced a £0 charge and still fed the headline returns | The short-let assessment is reported as incomplete (missing input) and excluded from the ranking; the year function raises rather than costing at zero. |
| `locality_score` gave 0.7 to any sale without a matching sector, including sales in other districts | Districts compared explicitly: same sector 1.0, same district 0.7, unknown postcode 0.4, different district 0.1. Tests for all four and for a mixed-district set. |
| `interest_only` flag existed but repayment was never modelled | Only `true` accepted, with the reason. |
| Single point estimate fragile near break-even | One-at-a-time sensitivity grid on every short-let assessment (occupancy, nightly rate, mortgage rate, operating costs). |
| Testing gaps: malformed inputs, extremes, independent arithmetic check | 14 new tests, including a line-by-line recomputation of the St Eval HL5-75 year from first principles (which caught the half-up vs banker's rounding difference between the test and the model, resolved in favour of the model's documented half-up). |

Reviewer's verdict on this round: 8/10, publishable as a portfolio project
demonstrating AI-assisted engineering with critical review; not a validated
investment calculator. The README now says exactly that.

## Reviewer's standing caveat, kept here on purpose

Passing tests prove the code behaves as tested. They do not establish that
the lending criteria, tax bands or council rules are still current, or that
a lender will approve a real application.

## Review 3 → commit 5

Reviewer's verdict on the previous state: 8.5/10, publishable. Two findings
and one presentation point, all three reproduced here against `0b227bd`
before anything was changed.

| Finding | Fix |
| --- | --- |
| The report warned that rental profit could push a basic-rate applicant into the higher band, while `lenders.py` ran the cover test at the configured band only. A marginal property could therefore pass on the 125% test and carry a warning that the 145% test might be the one that applies. | The year is now computed before eligibility, so the cover test can see the projected profit. Where the profit crosses the threshold **and** the lender's ratios differ by band, the rent is tested against both: clearing both is a pass, clearing only the lower one is reported as `cond` and the verdict line reads `ELIGIBLE, CONDITIONAL`, and the detail names both figures and says to ask the lender. A fail against the lower ratio stays a fail. |
| The sensitivity row labelled "operating costs +20%" scaled management, cleaning, utilities, maintenance and insurance — not the platform fee, council tax or business rates, or the estate charge. | Renamed to "variable costs", and the report prints what is varied and what is held underneath the grid. The scope of the calculation is unchanged: scaling a statutory council-tax charge or a contractual platform percentage by 20% models nothing, and the rates regime is decided in `rates.py` from evidence. A test asserts line by line that the five named lines move and the three named lines do not. |
| Break-even occupancy is pre-tax while the headline cash-on-cash is after tax, with no label to say so. | The report says "pre-tax break-even occupancy". A test asserts the bare label no longer appears anywhere in the output. |

Two corrections to the review, both verified in the code:

- The optimistic case it describes **does not occur in the committed worked
  example**, and could not. The only lender that accepts short lets (Leeds)
  publishes 145% for both bands, so its holiday-let cover test never depended
  on the band; and every Skipton long-let case in the example fails the 125%
  test already, and a stricter ratio cannot turn a fail into a pass. The
  defect was real but latent — it needed a marginal long let to surface — so
  the fix is carried by unit tests rather than by a change in the example.
  £375,000 at 6.44% stress requires £2,516/month at 125% and £2,918 at 145%:
  a rent of £2,700 is the conditional case, and that is the test.
- The warning had the opposite fault as well, which the review did not name:
  it fired on every Leeds row, where the band cannot change the cover test,
  and it quoted "the 145% cover test" as a constant rather than reading the
  lender's own figures. Income tax is computed progressively from the bands
  and never from `applicant.tax_band`, so the band flag drives nothing but
  the lender's ratio. Both wordings now come from the lender's config.

One recommendation accepted without change: the scope is frozen here. 61
tests, `ruff`, `ruff format` and `mypy --strict` clean, and the worked
example regenerated from the committed snapshots.

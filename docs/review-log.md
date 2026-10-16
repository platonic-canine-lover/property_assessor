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

## Reviewer's standing caveat, kept here on purpose

Passing tests prove the code behaves as tested. They do not establish that
the lending criteria, tax bands or council rules are still current, or that
a lender will approve a real application.

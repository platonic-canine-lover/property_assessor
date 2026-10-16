# property_assessor

Compare UK property candidates as investments, two ways at once: a standard
long let, and a short let on the Airbnb model. For each candidate, each
strategy and each lender product it answers three questions in order:

1. **Will anyone lend on it?** Every candidate is run through each lender's
   published criteria: accepted use, LTV, minimum value and income, and the
   rental-cover test at the lender's stress rate. A yield on a loan nobody
   will write is not a yield, so this comes first.
2. **What does it cost and return?** Cash in (25% deposit, SDLT with the
   additional-property surcharge, fees, furnishing for a short let), a
   year's income and operating costs, mortgage interest, tax under the
   individual-landlord rules, and four returns: gross yield, net yield,
   cash-on-cash, and for a short let the break-even occupancy.
3. **What does the number rest on?** Every input carries a basis
   (`published`, `stated`, `measured`, `estimate`, `assumption`) and a
   source. A derived figure inherits its weakest input's basis and the
   report prints it. Right now every return in the worked example is
   marked `assumption`, because the operating-cost percentages are
   assumptions, and the report says so rather than hiding it.

[![CI](https://github.com/platonic-canine-lover/property_assessor/actions/workflows/ci.yml/badge.svg)](https://github.com/platonic-canine-lover/property_assessor/actions/workflows/ci.yml)

## The worked example: two Cornwall listings, October 2026

| | [Little Carharrack, Redruth](https://www.rightmove.co.uk/properties/92648523) | [Vildebeest Road, St Eval](https://www.rightmove.co.uk/properties/87843999) |
| --- | --- | --- |
| Asking | offers over £500,000 (reduced 9 Sep 2026) | offers in excess of £285,000 |
| What | 2-bed detached bungalow, built 2024, TR16 | 3-bed semi, former RAF estate, PL27 |
| Council tax | band C | band B |
| Lenders | Skipton BTL (long let), Leeds holiday let (short let) | same |

```

uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m property_assessor.cli            # full report (docs/example-output.txt)
.venv/bin/python -m property_assessor.cli --json     # machine-readable
```

What the run says (ranked by year-1 cash-on-cash, eligible first; "s/s" is
the steady state once a year of letting is on record):

```
candidate                          strategy   lender / product                               elig     cash in  after-tax  CoC yr1  CoC s/s  gross    net basis
Vildebeest Road, St Eval, Wadebrid short_let  Leeds Building Society (holiday let) HL5-75    yes     £104,150      -£355    -0.3%     2.0%  13.6%   5.4% assumption
Vildebeest Road, St Eval, Wadebrid short_let  Leeds Building Society (holiday let) HL2-75    yes     £104,150      -£885    -0.8%     1.5%  13.6%   5.4% assumption
Vildebeest Road, St Eval, Wadebrid long_let   Skipton Building Society TRK2                  NO       £93,145    -£1,475    -1.6%        -   4.2%   3.0% assumption
Little Carharrack, Redruth (2-bed  short_let  Leeds Building Society (holiday let) HL5-75    NO      £179,400   -£10,001    -5.6%    -2.6%   7.8%   3.0% assumption
Little Carharrack, Redruth (2-bed  long_let   Skipton Building Society TRK2                  NO      £168,395    -£9,682    -5.8%        -   2.0%   1.5% assumption
```

Four findings, each of which the tool prints with the arithmetic behind it:

- **Skipton will not lend on a holiday let at all.** Its criteria list
  holiday lets and sub-letting as unacceptable, and it does not lend to
  limited companies. The brief assumed Skipton for both strategies; the
  short-let side needs a holiday-let lender, so Leeds Building Society's
  published holiday-let products are in the config as well.
- **The Redruth bungalow fails every cover test.** At 75% LTV the loan is
  £375,000. Skipton stresses a tracker at the higher of rate plus 2% or
  5.5% (6.44% on the chosen product) and wants rent at 125% of that
  interest: £2,516 a month. ONS's Cornwall average for a two-bed is £880.
  It is also a 2024 new build, and Skipton caps new builds with a developer
  incentive at 70% LTV until the incentive is ruled out. As a holiday let
  it misses Leeds' cover by £31 to £624 a month even on the county-wide
  ADR. Whatever it is worth as a home, it is not a 75%-LTV investment.
- **The St Eval house fails Skipton as a long let and clears Leeds as a
  short let, but year 1 is roughly break-even.** Long let: £1,077 ONS rent
  against £1,434 required (the gap closes if a letting agent will evidence
  higher). Short let: eligible on both Leeds products, but with no letting
  history the property pays doubled council tax in year 1 (£4,030), and the
  £19k of rental profit stacked on £45k of earned income runs into the 40%
  band, so after-tax cash flow is about zero. Once a year of letting is on
  record and business rates apply, cash-on-cash is 1.5% to 2.0%. Break-even
  occupancy is 52% in year 1 and 42% thereafter, against a county average
  of 57%.
- **The short-let figures are county-wide and the report says so on every
  line.** AirDNA's public Cornwall figures (ADR $242, 57% occupancy,
  trailing twelve months to September 2026) are dominated by coastal
  cottages. A semi on a former RAF estate inland is not that market. The
  honest next step is an AirDNA export or a holiday-letting agent's
  projection for St Eval specifically, which is also what Leeds will ask
  for; the config takes either.

## The Cornwall cliff, and why a projection cannot clear it

Cornwall charges a 100% council tax premium on second homes (since 1 April
2025). A holiday let escapes it only by qualifying for business rates, and
the test is historical: available 140 nights in the last twelve months,
available 140 nights in the next twelve, and actually let 70 nights in the
last twelve. A projection of next year's occupancy says nothing about last
year. So the model treats the three conditions separately (`rates.py`):

- **Year 1** rests on evidence the candidate carries (`short_let_history`,
  the seller's booking records). With none, council tax plus the premium
  applies and the report lists which conditions have no evidence.
- **Steady state** shows the regime once a full year at the projected
  occupancy is on record, if the owner's planned availability meets the
  threshold. For a band B property that is the difference between £4,030
  and (with small business rate relief assumed) £0 a year.

The break-even occupancy is computed per regime, with the regime held
fixed, because within one regime cash flow is monotone in occupancy and the
bisection is sound; across the threshold it is not, and the earlier version
of this tool got that wrong (see `docs/review-log.md`).

## Tax

Rental profit (income less operating costs, interest excluded) stacks on
top of earned income and is taxed through the 2026/27 bands with the
personal-allowance taper, so a profit that crosses £50,270 pays 40% on the
part above. Mortgage interest is not deductible; a 20% credit applies,
limited to the lower of the interest, the property profit and the tax due.
Two consequences the report shows rather than hides: a property with a cash
loss after interest can still owe tax, and a basic-rate earner can be pushed
into the higher band by the rental profit itself (the report warns, since
the lender may then apply the 145% cover test). The furnished-holiday-let
regime ended in April 2025, so short-let income is treated the same way.

## What is published and what is assumed

Published, with URL and date in the config: Skipton's criteria and product
guide (2 Oct 2026) and the tracker product chosen in its finder (16 Oct 2026);
Leeds' holiday-let products (21 Sep 2026); SDLT bands and the 5% surcharge
(GOV.UK); income tax rates; ONS average rents for Cornwall by bedroom count
(August 2026); AirDNA's public Cornwall figures (13 Oct 2026); Cornwall's
council tax bands for 2026/27, the second-home premium and the business-rates
thresholds; Rightmove's compilation of Land Registry sold prices for TR16 and
PL27 (to 31 July 2026).

Assumed, labelled as such on every line they touch: letting-agent and
holiday-management fees, cleaning, voids, maintenance, insurance,
utilities, furnishing, legal and survey fees, the GBP/USD rate (AirDNA
publishes in dollars), small business rate relief, Leeds' ICR and stress
rate (its product page points to a document that is not public; the
figures mirror Cumberland's published holiday-let test), and the
applicant's income. Replace any of them in `config/costs.yaml`,
`config/lenders/*.yaml` or `data/applicant.yaml` and the basis column
changes with it.

Not modelled: capital growth, exit costs, repayment mortgages (interest-only
is the BTL norm and the config flag exists), the Skipton tracker's base-rate
risk, planning restrictions on short lets (Cornwall has none county-wide at
the time of writing, but check the parish), and the estate covenants on a
former RAF estate.

## Layout

```
config/lenders/      skipton.yaml, leeds.yaml   criteria and products, sourced
config/tax.yaml      SDLT bands, income tax, FHL note
config/rules/        cornwall.yaml   council tax bands, premium, business-rates thresholds
config/costs.yaml    operating-cost assumptions
data/candidates.yaml the two listings as described
data/applicant.yaml  tax band, income, deposit
data/snapshots/      cornwall-2026-10.yaml   rents, ADR, occupancy, sold medians, comparables
src/property_assessor/
  sourced.py         Sourced values, basis ranking, provenance
  models.py          Candidate, Applicant
  lenders.py         criteria, stress rate, cover test, eligibility
  returns.py         SDLT, cash in, long-let and short-let year, tax, returns, break-even
  assessor.py        candidates × strategies × products
  report.py, cli.py
  rates.py           the three business-rates conditions, year 1 vs steady state
tests/               32 tests: lender maths, SDLT, year arithmetic, the three rates conditions, tax banding and the Section 24 outcome, break-even per regime, end to end
docs/example-output.txt
```

## Licence

BSD 2-Clause.

"""Purchase costs, operating costs and the return measures.

Four returns, because they answer different questions:

  gross yield        annual gross income ÷ purchase price
  net yield          (gross income − operating costs, before finance and tax) ÷ price
  cash-on-cash       after-tax cash flow ÷ cash actually invested
                     (deposit + SDLT with surcharge + fees + furnishing)
  break-even occupancy   short let only: the occupancy at which pre-tax cash
                     flow is zero, holding ADR and costs fixed

Tax follows the individual-landlord rules. Rental profit (income less
operating costs, interest excluded) stacks on top of earned income and is
taxed through the published bands, with the personal allowance taper; so a
profit that crosses £50,270 pays 40% on the part above. Mortgage interest is
not deductible; a 20% credit applies instead, limited to the lower of the
interest, the property profit, and the tax due. Short-let income is
ordinary property income (the FHL regime is gone).

Cornwall short-let council tax: which regime applies is decided in rates.py
from evidence, not from a projection, and passed in here explicitly. Within
a regime the cash flow is continuous in occupancy, which is what makes the
break-even search sound.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from property_assessor.config import AreaRules, CostConfig, MarketFigure, TaxConfig
from property_assessor.models import Applicant, Candidate, Strategy
from property_assessor.rates import RatesDecision
from property_assessor.sourced import Provenance, Sourced

P = Decimal("0.01")
NIGHTS = Decimal("365")


def money(d: Decimal) -> Decimal:
    return d.quantize(P, rounding=ROUND_HALF_UP)


def sdlt(price: Decimal, tax: TaxConfig, applicant: Applicant) -> Decimal:
    """Progressive bands plus the additional-property and non-resident surcharges."""
    total = Decimal(0)
    lower = Decimal(0)
    for band in tax.sdlt.bands:
        upper = Decimal(band.up_to) if band.up_to is not None else price
        portion = max(min(price, upper) - lower, Decimal(0))
        rate = band.rate
        if applicant.owns_other_residential_property:
            rate += tax.sdlt.additional_property_surcharge
        if not applicant.uk_resident:
            rate += tax.sdlt.non_resident_surcharge
        total += portion * rate
        lower = upper
        if band.up_to is None or price <= upper:
            break
    return money(total)


class Line(BaseModel):
    label: str
    amount: Decimal
    basis: str
    note: str | None = None


class CashIn(BaseModel):
    lines: list[Line]
    total: Decimal


class Year(BaseModel):
    strategy: Strategy
    gross_income: Decimal
    income_lines: list[Line]
    operating_lines: list[Line]
    operating_costs: Decimal
    mortgage_interest: Decimal
    pre_tax_cash_flow: Decimal
    tax: Decimal
    after_tax_cash_flow: Decimal
    provenance: Provenance


class Returns(BaseModel):
    gross_yield: Decimal
    net_yield: Decimal
    cash_on_cash: Decimal
    break_even_occupancy: Decimal | None = None
    council_tax_regime: str | None = None
    rates: RatesDecision | None = None
    provenance: Provenance


class Scenario(BaseModel):
    """A short let has two: year 1 (on the evidence) and steady state (once a year's letting is on record)."""

    name: str
    year: Year
    returns: Returns


class Assessment(BaseModel):
    candidate_id: str
    strategy: Strategy
    lender: str
    product: str
    loan: Decimal
    cash_in: CashIn
    year: Year
    returns: Returns
    scenarios: list[Scenario] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def cash_in(
    candidate: Candidate,
    applicant: Applicant,
    tax: TaxConfig,
    costs: CostConfig,
    product_fee: Sourced[Decimal],
    strategy: Strategy,
) -> CashIn:
    price = candidate.asking_price.value
    deposit = money(price * applicant.deposit_fraction)
    lines = [
        Line(label=f"deposit ({applicant.deposit_fraction:.0%})", amount=deposit, basis=candidate.asking_price.basis),
        Line(
            label="SDLT incl. additional-property surcharge" if applicant.owns_other_residential_property else "SDLT",
            amount=sdlt(price, tax, applicant),
            basis=tax.sdlt.basis,
            note=tax.sdlt.source,
        ),
        Line(label="product fee", amount=product_fee.value, basis=product_fee.basis),
        Line(label="legal fees", amount=costs.purchase.legal_fees.value, basis=costs.purchase.legal_fees.basis),
        Line(label="survey", amount=costs.purchase.survey.value, basis=costs.purchase.survey.basis),
        Line(
            label="mortgage valuation",
            amount=costs.purchase.mortgage_valuation.value,
            basis=costs.purchase.mortgage_valuation.basis,
        ),
    ]
    if strategy == "short_let":
        f = costs.short_let.furnishing_one_off
        lines.append(Line(label="furnishing and equipment", amount=f.value, basis=f.basis))
    return CashIn(lines=lines, total=money(sum((line.amount for line in lines), Decimal(0))))


def council_tax_annual(candidate: Candidate, rules: AreaRules) -> Decimal | None:
    band = candidate.council_tax_band
    if band is None:
        return None
    return rules.council_tax.annual_by_band.get(band)


def long_let_year(
    candidate: Candidate,
    applicant: Applicant,
    rent: MarketFigure,
    loan: Decimal,
    rate: Decimal,
    tax: TaxConfig,
    costs: CostConfig,
) -> Year:
    c = costs.long_let
    monthly = rent.value.value
    void_fraction = c.void_weeks_per_year.value / Decimal(52)
    gross = money(monthly * 12 * (1 - void_fraction))
    income_lines = [
        Line(
            label=f"rent £{monthly:,.0f}/month ({rent.granularity} figure, {rent.bedrooms or 'any'}-bed), less {c.void_weeks_per_year.value} void weeks",
            amount=gross,
            basis=rent.value.basis,
            note=rent.value.source,
        ),
    ]
    op = [
        Line(
            label=f"management {c.management_fee_fraction.value:.0%}",
            amount=money(gross * c.management_fee_fraction.value),
            basis=c.management_fee_fraction.basis,
        ),
        Line(
            label=f"maintenance {c.maintenance_fraction.value:.0%}",
            amount=money(gross * c.maintenance_fraction.value),
            basis=c.maintenance_fraction.basis,
        ),
        Line(label="landlord insurance", amount=c.landlord_insurance.value, basis=c.landlord_insurance.basis),
        Line(label="safety certificates", amount=c.safety_certificates.value, basis=c.safety_certificates.basis),
    ]
    if candidate.estate_charge_monthly is not None:
        op.append(
            Line(
                label="estate charge",
                amount=money(candidate.estate_charge_monthly.value * 12),
                basis=candidate.estate_charge_monthly.basis,
            )
        )
    return _finish_year(
        "long_let",
        gross,
        income_lines,
        op,
        loan,
        rate,
        tax,
        applicant,
        Provenance.of(rent=rent.value, management=c.management_fee_fraction, voids=c.void_weeks_per_year),
    )


def short_let_year(
    candidate: Candidate,
    applicant: Applicant,
    adr_usd: MarketFigure,
    occupancy: MarketFigure,
    loan: Decimal,
    rate: Decimal,
    tax: TaxConfig,
    costs: CostConfig,
    rules: AreaRules,
    decision: RatesDecision,
    occupancy_override: Decimal | None = None,
) -> Year:
    c = costs.short_let
    occ = occupancy_override if occupancy_override is not None else occupancy.value.value
    adr_gbp = adr_usd.value.value / costs.fx.usd_per_gbp.value
    nights = NIGHTS * occ
    gross = money(adr_gbp * nights)
    stays = (nights / c.average_stay_nights.value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    income_lines = [
        Line(
            label=f"ADR ${adr_usd.value.value:,.0f} ≈ £{adr_gbp:,.0f} × {nights:.0f} nights ({occ:.0%} occupancy, {occupancy.granularity} figure)",
            amount=gross,
            basis=adr_usd.value.basis,
            note=f"{adr_usd.value.source}; FX {costs.fx.usd_per_gbp}",
        )
    ]
    # Council tax or business rates: decided from evidence in rates.py, never from this projection.
    br = rules.business_rates_instead_of_council_tax
    ct = council_tax_annual(candidate, rules)
    if decision.regime == "business_rates":
        ct_line = Line(
            label="business rates (small business rate relief assumed)",
            amount=br.small_business_rate_relief.annual_charge_if_rated,
            basis=br.small_business_rate_relief.basis,
            note=f"{decision.reason}; {br.small_business_rate_relief.source}",
        )
    elif ct is None:
        ct_line = Line(
            label="council tax",
            amount=Decimal(0),
            basis="assumption",
            note="band not known; £0 entered, which is wrong",
        )
    else:
        premium = ct * (1 + rules.second_home_premium.multiplier)
        ct_line = Line(
            label=f"council tax band {candidate.council_tax_band} with {rules.second_home_premium.multiplier:.0%} second-home premium",
            amount=money(premium),
            basis=rules.second_home_premium.basis,
            note=f"{decision.reason}; {rules.second_home_premium.source}",
        )
    op = [
        Line(
            label=f"management {c.management_fee_fraction.value:.0%}",
            amount=money(gross * c.management_fee_fraction.value),
            basis=c.management_fee_fraction.basis,
        ),
        Line(
            label=f"platform fee {c.platform_fee_fraction.value:.0%}",
            amount=money(gross * c.platform_fee_fraction.value),
            basis=c.platform_fee_fraction.basis,
        ),
        Line(
            label=f"cleaning £{c.cleaning_per_stay.value:,.0f} × {stays} stays",
            amount=money(c.cleaning_per_stay.value * stays),
            basis=c.cleaning_per_stay.basis,
        ),
        Line(
            label="utilities (owner pays)",
            amount=money(c.utilities_monthly.value * 12),
            basis=c.utilities_monthly.basis,
        ),
        Line(
            label=f"maintenance {c.maintenance_fraction.value:.0%}",
            amount=money(gross * c.maintenance_fraction.value),
            basis=c.maintenance_fraction.basis,
        ),
        Line(label="holiday-let insurance", amount=c.insurance.value, basis=c.insurance.basis),
        ct_line,
    ]
    if candidate.estate_charge_monthly is not None:
        op.append(
            Line(
                label="estate charge",
                amount=money(candidate.estate_charge_monthly.value * 12),
                basis=candidate.estate_charge_monthly.basis,
            )
        )
    return _finish_year(
        "short_let",
        gross,
        income_lines,
        op,
        loan,
        rate,
        tax,
        applicant,
        Provenance.of(
            adr=adr_usd.value,
            occupancy=occupancy.value,
            fx=costs.fx.usd_per_gbp,
            management=c.management_fee_fraction,
            cleaning=c.cleaning_per_stay,
        ),
    )


def income_tax(taxable_income: Decimal, tax: TaxConfig) -> Decimal:
    """Tax on total income through the published bands, with the personal allowance taper."""
    it = tax.income_tax
    allowance = it.personal_allowance
    if taxable_income > it.personal_allowance_taper_from:
        allowance = max(allowance - (taxable_income - it.personal_allowance_taper_from) / 2, Decimal(0))
    remaining = max(taxable_income - allowance, Decimal(0))
    total = Decimal(0)
    lower = Decimal(0)
    for band in it.bands:
        upper = Decimal(band.up_to) if band.up_to is not None else remaining
        portion = max(min(remaining, upper) - lower, Decimal(0))
        total += portion * band.rate
        lower = upper
        if band.up_to is None or remaining <= upper:
            break
    return total


def individual_tax_on_profit(profit: Decimal, interest: Decimal, earned_income: Decimal, tax: TaxConfig) -> Decimal:
    """Extra tax the property causes: tax on (earned + profit) minus tax on earned alone, less the finance-cost credit.

    The credit is 20% of the lowest of: the interest, the property profit, and
    the taxable income above the allowance; and it cannot exceed the tax due.
    """
    profit = max(profit, Decimal(0))
    extra = income_tax(earned_income + profit, tax) - income_tax(earned_income, tax)
    credit_base = min(interest, profit, max(earned_income + profit - tax.income_tax.personal_allowance, Decimal(0)))
    credit = credit_base * tax.income_tax.finance_cost_credit
    return money(max(extra - credit, Decimal(0)))


def _finish_year(
    strategy: Strategy,
    gross: Decimal,
    income_lines: list[Line],
    op: list[Line],
    loan: Decimal,
    rate: Decimal,
    tax: TaxConfig,
    applicant: Applicant,
    provenance: Provenance,
) -> Year:
    operating = money(sum((line.amount for line in op), Decimal(0)))
    interest = money(loan * rate)
    pre_tax = money(gross - operating - interest)
    if applicant.limited_company:
        taxable = gross - operating - interest
        tax_due = money(max(taxable, Decimal(0)) * Decimal("0.25"))  # ASSUMPTION: main rate; small-profits rate ignored
    else:
        tax_due = individual_tax_on_profit(gross - operating, interest, applicant.gross_earned_income.value, tax)
    return Year(
        strategy=strategy,
        gross_income=gross,
        income_lines=income_lines,
        operating_lines=op,
        operating_costs=operating,
        mortgage_interest=interest,
        pre_tax_cash_flow=pre_tax,
        tax=tax_due,
        after_tax_cash_flow=money(pre_tax - tax_due),
        provenance=provenance,
    )


def returns(
    price: Decimal,
    year: Year,
    cash: CashIn,
    provenance: Provenance,
    break_even: Decimal | None = None,
    decision: RatesDecision | None = None,
) -> Returns:
    q = Decimal("0.0001")
    return Returns(
        gross_yield=(year.gross_income / price).quantize(q),
        net_yield=((year.gross_income - year.operating_costs) / price).quantize(q),
        cash_on_cash=(year.after_tax_cash_flow / cash.total).quantize(q),
        break_even_occupancy=break_even,
        council_tax_regime=f"{decision.scenario}: {decision.regime.replace('_', ' ')} ({decision.reason})" if decision else None,
        rates=decision,
        provenance=provenance,
    )


def break_even_occupancy(
    candidate: Candidate,
    applicant: Applicant,
    adr_usd: MarketFigure,
    occupancy: MarketFigure,
    loan: Decimal,
    rate: Decimal,
    tax: TaxConfig,
    costs: CostConfig,
    rules: AreaRules,
    decision: RatesDecision,
) -> Decimal | None:
    """Bisect occupancy until pre-tax cash flow crosses zero, holding the rates regime fixed.

    Within one regime the cash flow is monotone in occupancy (more nights, more
    income, more variable cost, but income per night exceeds cost per night),
    so bisection is sound. None if never positive at 100%.
    """

    def ptcf(o: Decimal) -> Decimal:
        y = short_let_year(candidate, applicant, adr_usd, occupancy, loan, rate, tax, costs, rules, decision, occupancy_override=o)
        return y.pre_tax_cash_flow

    lo, hi = Decimal("0"), Decimal("1")
    if ptcf(hi) < 0:
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if ptcf(mid) < 0:
            lo = mid
        else:
            hi = mid
    return hi.quantize(Decimal("0.001"))

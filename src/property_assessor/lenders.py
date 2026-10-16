"""Lender criteria and the eligibility test.

A lender is a config file (config/lenders/*.yaml) transcribed from its
published criteria with the URL and date. Each candidate is tested against
each lender for each strategy before any return is computed, because a
yield on a loan nobody will write is not a yield.

The tests, in order:
  1. property use accepted for the strategy (holiday lets are often excluded)
  2. applicant type accepted (limited company, portfolio landlord)
  3. minimum property value
  4. LTV within the product maximum (new-build incentive caps may apply)
  5. minimum earned income
  6. rental cover: rent must be at least ICR% of the interest at the stress
     rate, where the stress rate is max(product rate + uplift, floor) and
     the uplift depends on the product's term
Every failing test is reported with the number it failed by.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from property_assessor.models import Applicant, Candidate, Strategy, TaxBand
from property_assessor.sourced import Provenance, Sourced

TWOPLACES = Decimal("0.01")


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    kind: Literal["fixed", "tracker", "variable"]
    rate: Sourced[Decimal] = Field(description="Annual rate as a fraction, e.g. 0.0444")
    fee: Sourced[Decimal]
    term_years: int | None = Field(default=None, description="Initial period; None = unconfirmed")
    max_ltv: Decimal
    tracker_margin: Decimal | None = Field(default=None, description="For trackers: margin over base rate")
    base_rate_assumed: Sourced[Decimal] | None = Field(default=None, description="Base rate implied by the quoted tracker rate")
    note: str | None = None


class StressRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    floor: Decimal = Field(description="e.g. 0.055")
    uplift_short: Decimal = Field(description="added to product rate for fixes under long_fix_years, and trackers")
    uplift_long: Decimal = Field(description="added to product rate for fixes of long_fix_years or more")
    long_fix_years: int = 5


class Lender(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    criteria_url: str
    criteria_as_of: date
    products: list[Product]
    accepted_uses: list[Strategy]
    accepts_limited_company: bool
    accepts_portfolio_landlord: bool
    min_property_value: Sourced[Decimal]
    min_earned_income: Sourced[Decimal]
    max_ltv: Decimal
    new_build_incentive_max_ltv: Decimal | None = None
    icr: dict[TaxBand, Decimal] = Field(description="required rent / stressed interest, e.g. basic: 1.25")
    icr_portfolio: Decimal | None = None
    stress: StressRule
    notes: list[str] = Field(default_factory=list)

    def product(self, code: str) -> Product:
        for p in self.products:
            if p.code == code:
                return p
        raise KeyError(f"{self.name} has no product {code}")


def load_lender(path: Path) -> Lender:
    return Lender.model_validate(yaml.safe_load(path.read_text()))


def load_lenders(directory: Path) -> list[Lender]:
    return [load_lender(p) for p in sorted(directory.glob("*.yaml"))]


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class Eligibility(BaseModel):
    lender: str
    product: str
    strategy: Strategy
    checks: list[Check]
    loan: Decimal
    ltv: Decimal
    stress_rate: Decimal
    required_rent_monthly: Decimal
    available_rent_monthly: Decimal | None
    provenance: Provenance

    @property
    def eligible(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def money(d: Decimal) -> Decimal:
    return d.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def stress_rate(lender: Lender, product: Product) -> Decimal:
    long_fix = product.kind == "fixed" and product.term_years is not None and product.term_years >= lender.stress.long_fix_years
    uplift = lender.stress.uplift_long if long_fix else lender.stress.uplift_short
    return max(product.rate.value + uplift, lender.stress.floor)


def required_rent_monthly(lender: Lender, product: Product, applicant: Applicant, loan: Decimal) -> tuple[Decimal, Decimal]:
    rate = stress_rate(lender, product)
    icr = lender.icr_portfolio if applicant.portfolio_landlord and lender.icr_portfolio else lender.icr[applicant.tax_band]
    annual_interest = loan * rate
    return money(annual_interest * icr / 12), rate


def assess(
    lender: Lender,
    product: Product,
    candidate: Candidate,
    applicant: Applicant,
    strategy: Strategy,
    available_rent_monthly: Sourced[Decimal] | None,
) -> Eligibility:
    """Run every criterion and report each one, pass or fail."""
    price = candidate.asking_price.value
    loan = money(price * (1 - applicant.deposit_fraction))
    ltv = (loan / price).quantize(Decimal("0.0001"))
    checks: list[Check] = []

    checks.append(
        Check(
            name="property use",
            passed=strategy in lender.accepted_uses,
            detail=f"{strategy} {'is' if strategy in lender.accepted_uses else 'is NOT'} an accepted use ({lender.criteria_url})",
        )
    )
    if applicant.limited_company:
        checks.append(Check(name="limited company", passed=lender.accepts_limited_company, detail="limited company applicant"))
    if applicant.portfolio_landlord:
        checks.append(Check(name="portfolio landlord", passed=lender.accepts_portfolio_landlord, detail="portfolio landlord"))

    checks.append(
        Check(
            name="minimum property value",
            passed=price >= lender.min_property_value.value,
            detail=f"price £{price:,.0f} vs minimum £{lender.min_property_value.value:,.0f}",
        )
    )

    max_ltv = min(lender.max_ltv, product.max_ltv)
    if candidate.new_build and lender.new_build_incentive_max_ltv is not None and candidate.new_build_incentive is not False:
        max_ltv = min(max_ltv, lender.new_build_incentive_max_ltv)
        ltv_note = " (new build; incentive not ruled out, so the lower cap applies until confirmed)"
    else:
        ltv_note = ""
    checks.append(Check(name="LTV", passed=ltv <= max_ltv, detail=f"LTV {ltv:.1%} vs maximum {max_ltv:.0%}{ltv_note}"))

    checks.append(
        Check(
            name="minimum income",
            passed=applicant.gross_earned_income.value >= lender.min_earned_income.value,
            detail=f"earned income £{applicant.gross_earned_income.value:,.0f} vs minimum £{lender.min_earned_income.value:,.0f}",
        )
    )

    required, rate = required_rent_monthly(lender, product, applicant, loan)
    if available_rent_monthly is None:
        checks.append(
            Check(
                name="rental cover",
                passed=False,
                detail=f"requires £{required:,.0f}/month; no rent figure available to test",
            )
        )
        available = None
    else:
        available = available_rent_monthly.value
        gap = money(available - required)
        checks.append(
            Check(
                name="rental cover",
                passed=available >= required,
                detail=(
                    f"rent £{available:,.0f}/month vs required £{required:,.0f} "
                    f"({'passes by' if gap >= 0 else 'FAILS by'} £{abs(gap):,.0f}; stress {rate:.2%} × ICR {lender.icr_portfolio if applicant.portfolio_landlord and lender.icr_portfolio else lender.icr[applicant.tax_band]:.0%})"
                ),
            )
        )

    return Eligibility(
        lender=lender.name,
        product=f"{product.code} {product.name}",
        strategy=strategy,
        checks=checks,
        loan=loan,
        ltv=ltv,
        stress_rate=rate,
        required_rent_monthly=required,
        available_rent_monthly=available,
        provenance=Provenance.of(
            asking_price=candidate.asking_price,
            product_rate=product.rate,
            rent=available_rent_monthly,
            min_income=lender.min_earned_income,
        ),
    )

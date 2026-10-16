"""One-at-a-time sensitivity of a short-let year to its four most uncertain inputs.

A single point estimate is fragile when year-1 cash flow sits near zero, so
each short-let assessment carries a small grid: after-tax cash flow with
occupancy, nightly rate, mortgage rate and operating costs each moved on
their own, everything else held. It is a sensitivity table, not a
distribution: it says which input the answer depends on, not how likely
each value is.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from property_assessor.config import AreaRules, CostConfig, MarketFigure, TaxConfig
from property_assessor.models import Applicant, Candidate
from property_assessor.rates import RatesDecision
from property_assessor.returns import short_let_year
from property_assessor.sourced import Sourced


class SensitivityRow(BaseModel):
    input: str
    change: str
    after_tax_cash_flow: Decimal
    delta: Decimal


class Sensitivity(BaseModel):
    base_after_tax: Decimal
    rows: list[SensitivityRow]


def _scaled(fig: MarketFigure, factor: Decimal) -> MarketFigure:
    return fig.model_copy(update={"value": fig.value.model_copy(update={"value": (fig.value.value * factor).quantize(Decimal("0.0001"))})})


def _scaled_costs(costs: CostConfig, factor: Decimal) -> CostConfig:
    sl = costs.short_let

    def sc(x: Sourced[Decimal]) -> Sourced[Decimal]:
        return x.model_copy(update={"value": (x.value * factor).quantize(Decimal("0.0001"))})

    new_sl = sl.model_copy(
        update={
            "management_fee_fraction": sc(sl.management_fee_fraction),
            "cleaning_per_stay": sc(sl.cleaning_per_stay),
            "utilities_monthly": sc(sl.utilities_monthly),
            "maintenance_fraction": sc(sl.maintenance_fraction),
            "insurance": sc(sl.insurance),
        }
    )
    return costs.model_copy(update={"short_let": new_sl})


def short_let_sensitivity(
    candidate: Candidate,
    applicant: Applicant,
    adr: MarketFigure,
    occ: MarketFigure,
    loan: Decimal,
    rate: Decimal,
    tax: TaxConfig,
    costs: CostConfig,
    rules: AreaRules,
    decision: RatesDecision,
) -> Sensitivity:
    def cf(adr_f: MarketFigure = adr, occ_override: Decimal | None = None, r: Decimal = rate, c: CostConfig = costs) -> Decimal:
        return short_let_year(
            candidate, applicant, adr_f, occ, loan, r, tax, c, rules, decision, occupancy_override=occ_override
        ).after_tax_cash_flow

    base = cf()
    rows: list[SensitivityRow] = []

    def add(name: str, change: str, value: Decimal) -> None:
        rows.append(SensitivityRow(input=name, change=change, after_tax_cash_flow=value, delta=value - base))

    o = occ.value.value
    for pts in (Decimal("-0.15"), Decimal("-0.10"), Decimal("+0.10")):
        new = max(min(o + pts, Decimal(1)), Decimal(0))
        add("occupancy", f"{pts:+.0%} points ({new:.0%})", cf(occ_override=new))
    for f in (Decimal("0.85"), Decimal("1.15")):
        add("nightly rate", f"{f - 1:+.0%}", cf(adr_f=_scaled(adr, f)))
    for pts in (Decimal("0.01"), Decimal("0.02")):
        add("mortgage rate", f"{pts:+.2%} points ({rate + pts:.2%})", cf(r=rate + pts))
    for f in (Decimal("1.20"),):
        add("operating costs", f"{f - 1:+.0%}", cf(c=_scaled_costs(costs, f)))
    return Sensitivity(base_after_tax=base, rows=rows)

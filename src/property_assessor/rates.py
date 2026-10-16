"""Council tax or business rates for a short let: the Cornwall test, applied honestly.

A self-catering property moves from council tax (with the second-home
premium) to business rates only when all three conditions hold:

  1. available to let at least N nights in the LAST 12 months
  2. available to let at least N nights in the NEXT 12 months
  3. actually let at least M nights in the LAST 12 months

Conditions 1 and 3 are historical facts about the property. A projection of
next year's occupancy says nothing about them. So:

  * year 1 after purchase uses the evidence the candidate carries
    (`short_let_history`); with none, council tax plus premium applies and
    the report says why;
  * a steady-state scenario shows what happens once a year of letting at the
    projected occupancy is behind you, if that projection clears M and the
    owner plans at least N available nights.

Each condition is reported as met, not met, or unknown, with its figure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from property_assessor.config import AreaRules
from property_assessor.models import Candidate

Regime = Literal["council_tax_premium", "business_rates"]


class Condition(BaseModel):
    name: str
    required: int
    actual: int | None
    status: Literal["met", "not met", "unknown"]
    basis: str


class RatesDecision(BaseModel):
    scenario: str
    regime: Regime
    conditions: list[Condition]
    reason: str

    @property
    def qualifies(self) -> bool:
        return self.regime == "business_rates"


def _cond(name: str, required: int, actual: int | None, basis: str) -> Condition:
    if actual is None:
        status: Literal["met", "not met", "unknown"] = "unknown"
    else:
        status = "met" if actual >= required else "not met"
    return Condition(name=name, required=required, actual=actual, status=status, basis=basis)


def year_one(candidate: Candidate, rules: AreaRules, planned_available_next_12m: int, planned_basis: str) -> RatesDecision:
    """Regime for the first year of ownership: rests on evidence of the property's past letting."""
    br = rules.business_rates_instead_of_council_tax
    h = candidate.short_let_history
    conds = [
        _cond(
            "available nights, last 12 months",
            br.available_nights_last_12m,
            h.available_nights_last_12m.value if h else None,
            h.available_nights_last_12m.basis if h else "no evidence",
        ),
        _cond("available nights, next 12 months (owner's plan)", br.available_nights_next_12m, planned_available_next_12m, planned_basis),
        _cond(
            "let nights, last 12 months",
            br.let_nights_last_12m,
            h.let_nights_last_12m.value if h else None,
            h.let_nights_last_12m.basis if h else "no evidence",
        ),
    ]
    if all(c.status == "met" for c in conds):
        return RatesDecision(scenario="year 1", regime="business_rates", conditions=conds, reason="all three conditions evidenced")
    unknown = [c.name for c in conds if c.status == "unknown"]
    failed = [c.name for c in conds if c.status == "not met"]
    bits = []
    if unknown:
        bits.append("no evidence for: " + "; ".join(unknown))
    if failed:
        bits.append("not met: " + "; ".join(failed))
    return RatesDecision(scenario="year 1", regime="council_tax_premium", conditions=conds, reason="; ".join(bits))


def steady_state(
    projected_let_nights: Decimal, rules: AreaRules, planned_available_next_12m: int, planned_basis: str, projection_basis: str
) -> RatesDecision:
    """Regime once a full year at the projected occupancy is on record, if the owner keeps the property available."""
    br = rules.business_rates_instead_of_council_tax
    let_nights = int(projected_let_nights)
    conds = [
        _cond(
            "available nights, last 12 months (owner's plan, once a year has passed)",
            br.available_nights_last_12m,
            planned_available_next_12m,
            planned_basis,
        ),
        _cond("available nights, next 12 months (owner's plan)", br.available_nights_next_12m, planned_available_next_12m, planned_basis),
        _cond(
            "let nights, last 12 months (projected occupancy, once a year has passed)", br.let_nights_last_12m, let_nights, projection_basis
        ),
    ]
    if all(c.status == "met" for c in conds):
        return RatesDecision(
            scenario="steady state",
            regime="business_rates",
            conditions=conds,
            reason=f"would qualify after a year at {let_nights} let nights, if the plan holds",
        )
    failed = [c.name for c in conds if c.status != "met"]
    return RatesDecision(
        scenario="steady state", regime="council_tax_premium", conditions=conds, reason="would still not qualify: " + "; ".join(failed)
    )

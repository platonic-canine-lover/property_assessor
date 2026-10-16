"""Run every candidate through every strategy and every lender product."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from property_assessor.config import (
    AreaRules,
    CostConfig,
    Market,
    TaxConfig,
    load_area_rules,
    load_costs,
    load_market,
    load_tax,
)
from property_assessor.lenders import Eligibility, Lender, Product, assess, load_lenders
from property_assessor.models import Applicant, Candidate, Strategy
from property_assessor.rates import steady_state, year_one
from property_assessor.returns import (
    Assessment,
    Returns,
    Scenario,
    break_even_occupancy,
    cash_in,
    long_let_year,
    returns,
    short_let_year,
)
from property_assessor.sourced import Provenance, Sourced


@dataclass
class Inputs:
    tax: TaxConfig
    rules: AreaRules
    costs: CostConfig
    market: Market
    lenders: list[Lender]
    candidates: list[Candidate]
    applicant: Applicant

    @classmethod
    def load(cls, config_dir: Path, candidates_file: Path, applicant_file: Path, market_file: Path, area: str) -> Inputs:
        raw = yaml.safe_load(candidates_file.read_text())
        return cls(
            tax=load_tax(config_dir / "tax.yaml"),
            rules=load_area_rules(config_dir / "rules" / f"{area}.yaml"),
            costs=load_costs(config_dir / "costs.yaml"),
            market=load_market(market_file),
            lenders=load_lenders(config_dir / "lenders"),
            candidates=[Candidate.model_validate(c) for c in raw["candidates"]],
            applicant=Applicant.model_validate(yaml.safe_load(applicant_file.read_text())),
        )


@dataclass
class Result:
    candidate: Candidate
    strategy: Strategy
    lender: Lender
    product: Product
    eligibility: Eligibility
    assessment: Assessment | None
    missing: list[str]


def area_keys(c: Candidate) -> list[str]:
    """Most specific to least: full postcode, sector, district, town, then the county-wide fallbacks."""
    keys: list[str] = []
    if c.postcode:
        keys.append(c.postcode)
        keys.append(c.postcode.rsplit(" ", 1)[0] + " " + c.postcode.rsplit(" ", 1)[1][0])
    keys += [c.postcode_district, c.town, "Cornwall"]
    return keys


def run(inputs: Inputs, strategies: tuple[Strategy, ...] = ("long_let", "short_let")) -> list[Result]:
    out: list[Result] = []
    for cand in inputs.candidates:
        keys = area_keys(cand)
        rent = inputs.market.rent(keys, cand.bedrooms)
        adr = inputs.market.adr_usd(keys, cand.bedrooms)
        occ = inputs.market.occupancy(keys, cand.bedrooms)
        for strategy in strategies:
            for lender in inputs.lenders:
                for product in lender.products:
                    missing: list[str] = []
                    loan = cand.asking_price.value * (1 - inputs.applicant.deposit_fraction)
                    # Income figure the lender's cover test sees, and the year it implies.
                    available: Sourced[Decimal] | None = None
                    assessment: Assessment | None = None
                    if strategy == "long_let":
                        if rent is None:
                            missing.append("long-let rent for this area and bedroom count")
                        else:
                            available = rent.value
                    else:
                        if adr is None:
                            missing.append("short-let ADR for this area")
                        if occ is None:
                            missing.append("short-let occupancy for this area")
                        if adr is not None and occ is not None:
                            adr_gbp = adr.value.value / inputs.costs.fx.usd_per_gbp.value
                            monthly = (adr_gbp * Decimal(365) * occ.value.value / 12).quantize(Decimal("0.01"))
                            available = Sourced(
                                value=monthly,
                                basis=Provenance.of(adr=adr.value, occ=occ.value, fx=inputs.costs.fx.usd_per_gbp).basis,
                                source="projected gross short-let income / 12 (a lender will want a letting agent's seasonal projection)",
                            )
                    elig = assess(lender, product, cand, inputs.applicant, strategy, available)
                    if not missing:
                        rate = product.rate.value
                        cash = cash_in(cand, inputs.applicant, inputs.tax, inputs.costs, product.fee, strategy)
                        scenarios: list[Scenario] = []
                        if strategy == "long_let":
                            assert rent is not None
                            year = long_let_year(cand, inputs.applicant, rent, loan, rate, inputs.tax, inputs.costs)
                            ret: Returns = returns(cand.asking_price.value, year, cash, year.provenance)
                        else:
                            assert adr is not None and occ is not None
                            sl = inputs.costs.short_let
                            planned = sl.planned_available_nights_next_12m
                            projected_nights = Decimal(365) * occ.value.value
                            # Year 1 rests on evidence of past letting; with none, council tax + premium applies.
                            y1 = year_one(cand, inputs.rules, planned.value, planned.basis)
                            year = short_let_year(cand, inputs.applicant, adr, occ, loan, rate, inputs.tax, inputs.costs, inputs.rules, y1)
                            be1 = break_even_occupancy(
                                cand, inputs.applicant, adr, occ, loan, rate, inputs.tax, inputs.costs, inputs.rules, y1
                            )
                            ret = returns(cand.asking_price.value, year, cash, year.provenance, break_even=be1, decision=y1)
                            # Steady state: what the regime becomes after a year at the projected occupancy, if the plan holds.
                            ss = steady_state(projected_nights, inputs.rules, planned.value, planned.basis, occ.value.basis)
                            scenarios = []
                            if ss.regime != y1.regime:
                                y_ss = short_let_year(
                                    cand, inputs.applicant, adr, occ, loan, rate, inputs.tax, inputs.costs, inputs.rules, ss
                                )
                                be_ss = break_even_occupancy(
                                    cand, inputs.applicant, adr, occ, loan, rate, inputs.tax, inputs.costs, inputs.rules, ss
                                )
                                scenarios.append(
                                    Scenario(
                                        name="steady state",
                                        year=y_ss,
                                        returns=returns(
                                            cand.asking_price.value, y_ss, cash, y_ss.provenance, break_even=be_ss, decision=ss
                                        ),
                                    )
                                )
                        warnings: list[str] = []
                        if cand.holiday_let_restriction == "none_stated" and strategy == "short_let":
                            warnings.append(
                                "listing does not state whether holiday-let use is permitted; check the title and planning history"
                            )
                        if cand.private_water_or_drainage:
                            warnings.append(
                                "private water supply or drainage: lender valuation and holiday-let insurance may ask questions"
                            )
                        if not elig.eligible:
                            warnings.append("NOT ELIGIBLE with this lender and product; returns shown for information only")
                        profit = year.gross_income - year.operating_costs
                        it = inputs.tax.income_tax
                        higher_from = it.personal_allowance + Decimal(it.bands[0].up_to or 0)
                        total_income = inputs.applicant.gross_earned_income.value + profit
                        if inputs.applicant.tax_band == "basic" and total_income > higher_from:
                            warnings.append(
                                f"earned income plus rental profit (£{total_income:,.0f}) crosses the higher-rate threshold "
                                f"(£{higher_from:,.0f}); the lender may treat you as a higher-rate taxpayer and apply the 145% cover test"
                            )
                        assessment = Assessment(
                            candidate_id=cand.id,
                            strategy=strategy,
                            lender=lender.name,
                            product=f"{product.code} {product.name}",
                            loan=elig.loan,
                            cash_in=cash,
                            year=year,
                            returns=ret,
                            scenarios=scenarios,
                            warnings=warnings,
                        )
                    out.append(Result(cand, strategy, lender, product, elig, assessment, missing))
    return out

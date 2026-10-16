from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from property_assessor.assessor import Inputs, run
from property_assessor.config import MarketFigure, load_area_rules, load_costs, load_tax
from property_assessor.models import Applicant, Candidate
from property_assessor.rates import steady_state, year_one
from property_assessor.returns import (
    break_even_occupancy,
    cash_in,
    income_tax,
    individual_tax_on_profit,
    long_let_year,
    sdlt,
    short_let_year,
)
from property_assessor.sourced import Sourced

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


def s(value: str, basis: str = "stated") -> Sourced[Decimal]:
    return Sourced(value=Decimal(value), basis=basis, source="test", as_of=date(2026, 10, 16))  # type: ignore[arg-type]


@pytest.fixture
def tax():
    return load_tax(CONFIG / "tax.yaml")


@pytest.fixture
def costs():
    return load_costs(CONFIG / "costs.yaml")


@pytest.fixture
def rules():
    return load_area_rules(CONFIG / "rules" / "cornwall.yaml")


@pytest.fixture
def buyer():
    return Applicant(tax_band="basic", gross_earned_income=s("45000"))


def cand(price="285000", band="B", **kw) -> Candidate:
    base = dict(
        id="c",
        label="c",
        listing_url="https://example.invalid",
        postcode_district="PL27",
        town="St Eval",
        asking_price=s(price),
        bedrooms=3,
        property_type="semi_detached",
        tenure="freehold",
        council_tax_band=band,
    )
    base.update(kw)
    return Candidate(**base)


class TestSdlt:
    def test_additional_property_on_285k(self, tax, buyer):
        # 125k×5% + 125k×7% + 35k×10% = 6,250 + 8,750 + 3,500
        assert sdlt(Decimal("285000"), tax, buyer) == Decimal("18500.00")

    def test_additional_property_on_500k(self, tax, buyer):
        assert sdlt(Decimal("500000"), tax, buyer) == Decimal("40000.00")

    def test_only_property_pays_standard_rates(self, tax, buyer):
        only = buyer.model_copy(update={"owns_other_residential_property": False})
        # 125k×0 + 125k×2% + 35k×5% = 2,500 + 1,750
        assert sdlt(Decimal("285000"), tax, only) == Decimal("4250.00")

    def test_non_resident_adds_two_percent(self, tax, buyer):
        nr = buyer.model_copy(update={"uk_resident": False})
        assert sdlt(Decimal("285000"), tax, nr) == Decimal("18500.00") + Decimal("285000") * Decimal("0.02")

    def test_top_band(self, tax, buyer):
        only = buyer.model_copy(update={"owns_other_residential_property": False})
        # 2m: 0 + 2,500 + 33,750 + 57,500 + 60,000
        assert sdlt(Decimal("2000000"), tax, only) == Decimal("153750.00")


class TestCashIn:
    def test_short_let_adds_furnishing(self, tax, costs, buyer):
        long = cash_in(cand(), buyer, tax, costs, s("0"), "long_let")
        short = cash_in(cand(), buyer, tax, costs, s("0"), "short_let")
        assert long.total == Decimal("92150.00")  # 71,250 + 18,500 + 1,800 + 600
        assert short.total == long.total + costs.short_let.furnishing_one_off.value
        assert {li.basis for li in long.lines} >= {"stated", "published", "assumption"}


class TestLongLet:
    def test_year_arithmetic(self, tax, costs, buyer):
        rent = MarketFigure(area="Cornwall", bedrooms=3, granularity="local_authority", value=s("1077", "published"))
        y = long_let_year(cand(estate_charge_monthly=s("40")), buyer, rent, Decimal("213750"), Decimal("0.0444"), tax, costs)
        assert y.gross_income == Decimal("11929.85")  # 1077×12×(48/52)
        # 10% (1,192.99) + 8% (954.39) of gross + 350 + 250 + 480 estate charge, each line rounded
        assert y.operating_costs == Decimal("3227.38")
        assert y.mortgage_interest == Decimal("9490.50")
        assert y.pre_tax_cash_flow == Decimal("-788.03")
        # profit 8,702.47 stacks on £45,000 earned: 5,270 at 20% + 3,432.47 at 40% = 2,426.99;
        # credit 20% × min(interest 9,490.50, profit 8,702.47) = 1,740.49 → 686.49.
        # Cash flow is negative and tax is still due: that is Section 24, not a bug.
        assert y.tax == Decimal("686.49")
        assert y.provenance.basis == "assumption"

    def test_tenant_pays_council_tax_so_none_in_operating(self, tax, costs, buyer):
        rent = MarketFigure(area="Cornwall", bedrooms=3, granularity="local_authority", value=s("1077", "published"))
        y = long_let_year(cand(), buyer, rent, Decimal("1"), Decimal("0.05"), tax, costs)
        assert not any("council tax" in li.label for li in y.operating_lines)


def premium(rules):
    return year_one(cand(), rules, 300, "assumption")  # no history → council tax + premium


def rated(rules):
    return steady_state(Decimal("208"), rules, 300, "assumption", "published")  # would qualify


class TestShortLet:
    adr = MarketFigure(area="Cornwall", granularity="county", value=s("242", "published"))
    occ = MarketFigure(area="Cornwall", granularity="county", value=s("0.57", "published"))

    def test_year_arithmetic_under_business_rates(self, tax, costs, buyer, rules):
        y = short_let_year(cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, rated(rules))
        # ADR 242/1.30 = 186.1538… × 365 × 0.57 = 208.05 nights
        assert y.gross_income == Decimal("38729.31")
        ct = next(li for li in y.operating_lines if "business rates" in li.label)
        assert ct.amount == Decimal("0") and ct.basis == "assumption"

    def test_year_one_without_history_pays_doubled_council_tax_even_at_high_occupancy(self, tax, costs, buyer, rules):
        # The projection says 208 let nights; the historical test has no evidence, so the premium applies.
        y = short_let_year(
            cand(band="B"), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, premium(rules)
        )
        ct = next(li for li in y.operating_lines if "second-home premium" in li.label)
        assert ct.amount == Decimal("4030.32")  # 2,015.16 × 2
        assert ct.basis == "published"
        assert "no evidence" in (ct.note or "")

    def test_regime_is_never_inferred_from_the_projection(self, tax, costs, buyer, rules):
        # Same inputs, two decisions, two cost bases: the decision is an input, not a side effect of occupancy.
        a = short_let_year(cand(), buyer, self.adr, self.occ, Decimal("1"), Decimal("0.05"), tax, costs, rules, premium(rules))
        b = short_let_year(cand(), buyer, self.adr, self.occ, Decimal("1"), Decimal("0.05"), tax, costs, rules, rated(rules))
        assert a.operating_costs - b.operating_costs == Decimal("4030.32")

    def test_unknown_council_tax_band_is_flagged_not_silently_zero(self, tax, costs, buyer, rules):
        y = short_let_year(cand(band=None), buyer, self.adr, self.occ, Decimal("1"), Decimal("0.05"), tax, costs, rules, premium(rules))
        assert any("wrong" in (li.note or "") for li in y.operating_lines)

    def test_break_even_is_regime_specific_and_cash_flow_is_monotone_within_a_regime(self, tax, costs, buyer, rules):
        be_p = break_even_occupancy(
            cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, premium(rules)
        )
        be_r = break_even_occupancy(
            cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, rated(rules)
        )
        assert be_p is not None and be_r is not None
        assert be_p > be_r  # doubled council tax needs more nights to cover
        for dec, be in ((premium(rules), be_p), (rated(rules), be_r)):
            at = short_let_year(
                cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, dec, occupancy_override=be
            )
            assert abs(at.pre_tax_cash_flow) < Decimal("50")
            # monotone: every step up in occupancy raises pre-tax cash flow
            prev = None
            for k in range(0, 101, 5):
                o = Decimal(k) / 100
                v = short_let_year(
                    cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, dec, occupancy_override=o
                ).pre_tax_cash_flow
                assert prev is None or v >= prev
                prev = v

    def test_break_even_none_when_never_positive(self, tax, costs, buyer, rules):
        tiny = MarketFigure(area="Cornwall", granularity="county", value=s("20", "published"))
        assert (
            break_even_occupancy(cand(), buyer, tiny, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules, rated(rules))
            is None
        )


class TestRates:
    def test_year_one_needs_evidence_for_the_two_historical_conditions(self, rules):
        d = year_one(cand(), rules, 300, "assumption")
        assert d.regime == "council_tax_premium"
        assert [c.status for c in d.conditions] == ["unknown", "met", "unknown"]
        assert "no evidence" in d.reason

    def test_year_one_qualifies_only_with_evidence_meeting_all_three(self, rules):
        from property_assessor.models import ShortLetHistory

        hist = ShortLetHistory(
            available_nights_last_12m=Sourced(value=200, basis="stated", source="seller's booking records"),
            let_nights_last_12m=Sourced(value=95, basis="stated", source="seller's booking records"),
        )
        assert year_one(cand(short_let_history=hist), rules, 300, "assumption").regime == "business_rates"
        short = hist.model_copy(update={"let_nights_last_12m": Sourced(value=69, basis="stated", source="s")})
        d = year_one(cand(short_let_history=short), rules, 300, "assumption")
        assert d.regime == "council_tax_premium" and "not met: let nights" in d.reason
        assert year_one(cand(short_let_history=hist), rules, 100, "assumption").regime == "council_tax_premium"  # plan too low

    def test_steady_state_uses_the_projection_and_the_plan(self, rules):
        assert steady_state(Decimal("70"), rules, 140, "assumption", "published").regime == "business_rates"
        assert steady_state(Decimal("69.9"), rules, 300, "assumption", "published").regime == "council_tax_premium"
        assert steady_state(Decimal("300"), rules, 139, "assumption", "published").regime == "council_tax_premium"


class TestTax:
    def test_income_tax_bands_and_taper(self, tax):
        assert income_tax(Decimal("12570"), tax) == Decimal("0")
        assert income_tax(Decimal("50270"), tax) == Decimal("7540")  # 37,700 × 20%
        assert income_tax(Decimal("60270"), tax) == Decimal("11540")  # + 10,000 × 40%
        # taper: £110,000 loses £5,000 of allowance
        assert income_tax(Decimal("110000"), tax) == Decimal("37700") * Decimal("0.20") + (
            Decimal("110000") - Decimal("7570") - Decimal("37700")
        ) * Decimal("0.40")

    def test_rental_profit_stacks_on_earned_income_and_crosses_into_higher_rate(self, tax):
        # £45,000 earned; £15,293 profit: 5,270 at 20% + 10,023 at 40% = 1,054 + 4,009.20 = 5,063.20
        # credit 20% × min(interest 13,231, profit) = 2,646.20 → 2,417.00
        assert individual_tax_on_profit(Decimal("15293"), Decimal("13231"), Decimal("45000"), tax) == Decimal("2417.00")
        # a basic-rate earner with a small profit stays at 20%
        assert individual_tax_on_profit(Decimal("5000"), Decimal("1000"), Decimal("30000"), tax) == Decimal("800.00")

    def test_finance_cost_credit_is_capped_by_profit_and_by_tax_due(self, tax):
        # profit £1,000, interest £20,000: credit limited to 20% of £1,000 = £200, so tax = 200 − 200 = 0, never negative
        assert individual_tax_on_profit(Decimal("1000"), Decimal("20000"), Decimal("30000"), tax) == Decimal("0")
        # no profit → no tax, no refund
        assert individual_tax_on_profit(Decimal("-500"), Decimal("20000"), Decimal("30000"), tax) == Decimal("0")

    def test_section_24_a_cash_loss_can_still_owe_tax(self, tax, costs, buyer):
        # Interest is not deductible: profit before interest is £9,182, cash flow after interest is -£4,711,
        # and the 20% credit does not cover the 40%-band tax on the part of the profit above £50,270.
        rent = MarketFigure(area="Cornwall", bedrooms=3, granularity="local_authority", value=s("1077", "published"))
        y = long_let_year(cand(), buyer, rent, Decimal("213750"), Decimal("0.0650"), tax, costs)
        assert y.pre_tax_cash_flow < 0
        assert y.tax > 0
        # a basic-rate earner with headroom pays nothing on the same property: the credit covers the 20% tax
        low = buyer.model_copy(update={"gross_earned_income": s("25000")})
        assert long_let_year(cand(), low, rent, Decimal("213750"), Decimal("0.0650"), tax, costs).tax == Decimal("0")


class TestEndToEnd:
    def test_worked_example_runs_and_reports_known_findings(self):
        inputs = Inputs.load(
            CONFIG,
            ROOT / "data" / "candidates.yaml",
            ROOT / "data" / "applicant.yaml",
            ROOT / "data" / "snapshots" / "cornwall-2026-10.yaml",
            "cornwall",
        )
        results = run(inputs)
        assert len(results) == 2 * 2 * 6  # candidates × strategies × products
        # every short-let assessment carries a rates decision whose year-1 regime rests on evidence, not the projection
        for r in results:
            if r.strategy == "short_let" and r.assessment:
                assert r.assessment.returns.rates is not None
                assert r.assessment.returns.rates.regime == "council_tax_premium"  # neither listing has letting history
                assert r.assessment.scenarios and r.assessment.scenarios[0].returns.rates.regime == "business_rates"
        redruth_long = [
            r for r in results if r.candidate.id == "redruth-bungalow" and r.strategy == "long_let" and r.lender.id == "skipton"
        ]
        assert all(not r.eligibility.eligible for r in redruth_long)
        assert all(any(c.name == "rental cover" and not c.passed for c in r.eligibility.checks) for r in redruth_long)
        st_eval_short_leeds = [
            r for r in results if r.candidate.id == "st-eval-semi" and r.strategy == "short_let" and r.lender.id == "leeds"
        ]
        assert all(r.eligibility.eligible for r in st_eval_short_leeds)
        # the returns' basis is honest: assumptions are in the chain, so the figure is an assumption
        assert all(r.assessment.returns.provenance.basis == "assumption" for r in results if r.assessment)

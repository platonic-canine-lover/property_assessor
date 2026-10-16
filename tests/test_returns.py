from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from property_assessor.assessor import Inputs, run
from property_assessor.config import MarketFigure, load_area_rules, load_costs, load_tax
from property_assessor.models import Applicant, Candidate
from property_assessor.returns import break_even_occupancy, cash_in, long_let_year, sdlt, short_let_year
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
        # taxable 8,702.48 × 20% = 1,740.50; credit 20% × 9,490.50 = 1,898.10 → tax 0 (credit capped at tax due)
        assert y.tax == Decimal("0")
        assert y.provenance.basis == "assumption"

    def test_tenant_pays_council_tax_so_none_in_operating(self, tax, costs, buyer):
        rent = MarketFigure(area="Cornwall", bedrooms=3, granularity="local_authority", value=s("1077", "published"))
        y = long_let_year(cand(), buyer, rent, Decimal("1"), Decimal("0.05"), tax, costs)
        assert not any("council tax" in li.label for li in y.operating_lines)


class TestShortLet:
    adr = MarketFigure(area="Cornwall", granularity="county", value=s("242", "published"))
    occ = MarketFigure(area="Cornwall", granularity="county", value=s("0.57", "published"))

    def test_year_arithmetic_and_business_rates_regime(self, tax, costs, buyer, rules):
        y, regime = short_let_year(cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules)
        # ADR 242/1.30 = 186.1538… × 365 × 0.57 = 208.05 nights
        assert y.gross_income == Decimal("38729.31")
        assert regime.startswith("business rates")
        ct = next(li for li in y.operating_lines if "business rates" in li.label)
        assert ct.amount == Decimal("0") and ct.basis == "assumption"

    def test_below_70_let_nights_pays_doubled_council_tax(self, tax, costs, buyer, rules):
        low = MarketFigure(area="Cornwall", granularity="county", value=s("0.15", "published"))  # 55 nights
        y, regime = short_let_year(cand(band="B"), buyer, self.adr, low, Decimal("213750"), Decimal("0.0650"), tax, costs, rules)
        assert regime.startswith("council tax + premium")
        ct = next(li for li in y.operating_lines if "second-home premium" in li.label)
        assert ct.amount == Decimal("4030.32")  # 2,015.16 × 2
        assert ct.basis == "published"

    def test_the_cliff_is_visible_either_side_of_70_nights(self, tax, costs, buyer, rules):
        just_under = MarketFigure(area="Cornwall", granularity="county", value=s("0.19", "published"))  # 69.35 nights
        just_over = MarketFigure(area="Cornwall", granularity="county", value=s("0.192", "published"))  # 70.08 nights
        under, _ = short_let_year(cand(), buyer, self.adr, just_under, Decimal("213750"), Decimal("0.065"), tax, costs, rules)
        over, _ = short_let_year(cand(), buyer, self.adr, just_over, Decimal("213750"), Decimal("0.065"), tax, costs, rules)
        # one more night of lets removes a £4,030 charge
        assert under.operating_costs - over.operating_costs > Decimal("3900")

    def test_unknown_council_tax_band_is_flagged_not_silently_zero(self, tax, costs, buyer, rules):
        low = MarketFigure(area="Cornwall", granularity="county", value=s("0.10", "published"))
        y, regime = short_let_year(cand(band=None), buyer, self.adr, low, Decimal("1"), Decimal("0.05"), tax, costs, rules)
        assert regime == "unknown band"
        assert any("wrong" in (li.note or "") for li in y.operating_lines)

    def test_break_even_occupancy_brackets_zero_cash_flow(self, tax, costs, buyer, rules):
        be = break_even_occupancy(cand(), buyer, self.adr, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules)
        assert be is not None and Decimal("0.40") < be < Decimal("0.46")
        y_at, _ = short_let_year(
            cand(),
            buyer,
            self.adr,
            self.occ,
            Decimal("213750"),
            Decimal("0.0650"),
            tax,
            costs,
            rules,
            occupancy_override=be,
        )
        assert abs(y_at.pre_tax_cash_flow) < Decimal("50")

    def test_break_even_none_when_never_positive(self, tax, costs, buyer, rules):
        tiny = MarketFigure(area="Cornwall", granularity="county", value=s("20", "published"))
        assert break_even_occupancy(cand(), buyer, tiny, self.occ, Decimal("213750"), Decimal("0.0650"), tax, costs, rules) is None


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

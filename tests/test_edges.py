"""Review 2: malformed inputs, locality edge cases, extremes, scope enforcement, sensitivity, headline arithmetic."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from property_assessor.assessor import Inputs, comparables, run
from property_assessor.comparables import Sale, SalesSet, compare, locality_score
from property_assessor.config import MarketFigure, load_area_rules, load_costs, load_tax
from property_assessor.models import Applicant, Candidate
from property_assessor.rates import year_one
from property_assessor.returns import short_let_year
from property_assessor.sensitivity import short_let_sensitivity
from property_assessor.sourced import Sourced

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
TODAY = date(2026, 10, 16)


def s(v: str, basis: str = "stated") -> Sourced[Decimal]:
    return Sourced(value=Decimal(v), basis=basis, source="t")  # type: ignore[arg-type]


def cand(**kw) -> Candidate:
    base = dict(
        id="c",
        label="c",
        listing_url="https://example.invalid",
        postcode_district="PL27",
        town="St Eval",
        asking_price=s("285000"),
        bedrooms=3,
        property_type="semi_detached",
        tenure="freehold",
        council_tax_band="B",
    )
    base.update(kw)
    return Candidate(**base)


class TestScope:
    def test_limited_company_is_rejected_with_a_reason(self):
        with pytest.raises(ValidationError, match="corporation tax"):
            Applicant(tax_band="basic", gross_earned_income=s("45000"), limited_company=True)

    def test_repayment_mortgage_is_rejected_with_a_reason(self):
        with pytest.raises(ValidationError, match="capital repayment"):
            Applicant(tax_band="basic", gross_earned_income=s("45000"), interest_only=False)

    def test_unknown_council_tax_band_makes_the_short_let_assessment_incomplete(self):
        inputs = Inputs.load(
            CONFIG,
            ROOT / "data/candidates.yaml",
            ROOT / "data/applicant.yaml",
            ROOT / "data/snapshots/cornwall-2026-10.yaml",
            "cornwall",
            today=TODAY,
        )
        no_band = inputs.candidates[1].model_copy(update={"council_tax_band": None})
        inputs.candidates = [no_band]
        results = run(inputs)
        short = [r for r in results if r.strategy == "short_let"]
        assert short and all(r.assessment is None for r in short)
        assert all(any("council tax band" in m for m in r.missing) for r in short)
        # the year function refuses outright rather than costing at £0
        tax, costs, rules = (
            load_tax(CONFIG / "tax.yaml"),
            load_costs(CONFIG / "costs.yaml"),
            load_area_rules(CONFIG / "rules/cornwall.yaml"),
        )
        adr = MarketFigure(area="Cornwall", granularity="county", value=s("242", "published"))
        occ = MarketFigure(area="Cornwall", granularity="county", value=s("0.57", "published"))
        with pytest.raises(ValueError, match="council tax band"):
            short_let_year(
                cand(council_tax_band=None),
                Applicant(tax_band="basic", gross_earned_income=s("45000")),
                adr,
                occ,
                Decimal("1"),
                Decimal("0.05"),
                tax,
                costs,
                rules,
                year_one(cand(), rules, 300, "assumption"),
            )


class TestMalformedInputs:
    def test_bad_postcode_district(self):
        with pytest.raises(ValidationError):
            cand(postcode_district="not a district")

    def test_negative_bedrooms(self):
        with pytest.raises(ValidationError):
            cand(bedrooms=-1)

    def test_deposit_fraction_bounds(self):
        with pytest.raises(ValidationError):
            Applicant(tax_band="basic", gross_earned_income=s("45000"), deposit_fraction=Decimal("1.5"))

    def test_unknown_basis_rejected(self):
        with pytest.raises(ValidationError):
            Sourced(value=Decimal("1"), basis="guess", source="t")  # type: ignore[arg-type]


class TestLocality:
    def sale(self, pc):
        return Sale(price=Decimal("1"), date=TODAY, address="x", postcode=pc, type="semi_detached", beds=3)

    def test_four_cases(self):
        c = cand(postcode="PL27 7RR")
        assert locality_score(c, self.sale("PL27 7AA")) == Decimal("1.0")  # same sector
        assert locality_score(c, self.sale("PL27 6AA")) == Decimal("0.7")  # same district, other sector
        assert locality_score(c, self.sale("TR16 4AA")) == Decimal("0.1")  # different district
        assert locality_score(c, self.sale(None)) == Decimal("0.4")  # unknown
        # candidate with district only: same district 0.7, other district still 0.1
        d = cand()
        assert locality_score(d, self.sale("PL27 7AA")) == Decimal("0.7")
        assert locality_score(d, self.sale("TR16 4AA")) == Decimal("0.1")

    def test_mixed_district_set_does_not_let_distant_sales_dominate(self):
        near = [Sale(price=Decimal(300000), date=TODAY, address="x", postcode=f"PL27 7A{i}", type="semi_detached", beds=3) for i in "ABCD"]
        far = [
            Sale(price=Decimal(900000), date=TODAY, address="y", postcode=f"TR16 4A{i}", type="semi_detached", beds=3) for i in "ABCDEFGH"
        ]
        res = compare(cand(postcode="PL27 7RR"), SalesSet(source="s", as_of=TODAY, basis="published", sales=near + far), TODAY)
        assert res.weighted_median == Decimal(300000)


class TestExtremes:
    def test_very_low_price_fails_minimum_value_not_arithmetic(self):
        inputs = Inputs.load(
            CONFIG,
            ROOT / "data/candidates.yaml",
            ROOT / "data/applicant.yaml",
            ROOT / "data/snapshots/cornwall-2026-10.yaml",
            "cornwall",
            today=TODAY,
        )
        cheap = inputs.candidates[1].model_copy(update={"asking_price": s("50000")})
        inputs.candidates = [cheap]
        skipton = [r for r in run(inputs) if r.lender.id == "skipton" and r.strategy == "long_let"]
        assert all(any(c.name == "minimum property value" and not c.passed for c in r.eligibility.checks) for r in skipton)

    def test_zero_and_full_occupancy(self):
        tax, costs, rules = (
            load_tax(CONFIG / "tax.yaml"),
            load_costs(CONFIG / "costs.yaml"),
            load_area_rules(CONFIG / "rules/cornwall.yaml"),
        )
        adr = MarketFigure(area="Cornwall", granularity="county", value=s("242", "published"))
        occ = MarketFigure(area="Cornwall", granularity="county", value=s("0.57", "published"))
        buyer = Applicant(tax_band="basic", gross_earned_income=s("45000"))
        dec = year_one(cand(), rules, 300, "assumption")
        zero = short_let_year(
            cand(), buyer, adr, occ, Decimal("213750"), Decimal("0.065"), tax, costs, rules, dec, occupancy_override=Decimal(0)
        )
        full = short_let_year(
            cand(), buyer, adr, occ, Decimal("213750"), Decimal("0.065"), tax, costs, rules, dec, occupancy_override=Decimal(1)
        )
        assert zero.gross_income == Decimal(0) and zero.tax == Decimal(0) and zero.pre_tax_cash_flow < 0
        assert full.gross_income > zero.gross_income and full.after_tax_cash_flow > zero.after_tax_cash_flow

    def test_absurd_mortgage_rate_still_computes(self):
        inputs = Inputs.load(
            CONFIG,
            ROOT / "data/candidates.yaml",
            ROOT / "data/applicant.yaml",
            ROOT / "data/snapshots/cornwall-2026-10.yaml",
            "cornwall",
            today=TODAY,
        )
        lender = inputs.lenders[1]  # skipton
        hot = lender.products[0].model_copy(update={"rate": s("0.30", "published")})
        inputs.lenders = [lender.model_copy(update={"products": [hot]})]
        results = run(inputs, strategies=("long_let",))
        assert all(r.assessment is not None and r.assessment.year.after_tax_cash_flow < 0 for r in results)


class TestSensitivity:
    def test_grid_moves_in_the_expected_directions(self):
        tax, costs, rules = (
            load_tax(CONFIG / "tax.yaml"),
            load_costs(CONFIG / "costs.yaml"),
            load_area_rules(CONFIG / "rules/cornwall.yaml"),
        )
        adr = MarketFigure(area="Cornwall", granularity="county", value=s("242", "published"))
        occ = MarketFigure(area="Cornwall", granularity="county", value=s("0.57", "published"))
        buyer = Applicant(tax_band="basic", gross_earned_income=s("45000"))
        sens = short_let_sensitivity(
            cand(), buyer, adr, occ, Decimal("213750"), Decimal("0.065"), tax, costs, rules, year_one(cand(), rules, 300, "assumption")
        )
        by = {(r.input, r.change): r.delta for r in sens.rows}
        assert by[("occupancy", "+10% points (67%)")] > 0 > by[("occupancy", "-10% points (47%)")]
        assert by[("nightly rate", "+15%")] > 0 > by[("nightly rate", "-15%")]
        assert by[("mortgage rate", "+1.00% points (7.50%)")] < 0
        assert by[("mortgage rate", "+2.00% points (8.50%)")] < by[("mortgage rate", "+1.00% points (7.50%)")]
        assert by[("operating costs", "+20%")] < 0
        # a 1-point rate rise on £213,750 interest-only costs £2,137.50 pre-tax; after the 20% credit about £1,710
        assert Decimal("-2200") < by[("mortgage rate", "+1.00% points (7.50%)")] < Decimal("-1600")


class TestHeadlineArithmetic:
    """Independent check of the worked example's St Eval short-let, Leeds HL5-75, year 1."""

    def test_st_eval_leeds_hl5_year_one(self):
        inputs = Inputs.load(
            CONFIG,
            ROOT / "data/candidates.yaml",
            ROOT / "data/applicant.yaml",
            ROOT / "data/snapshots/cornwall-2026-10.yaml",
            "cornwall",
            today=TODAY,
        )
        r = next(x for x in run(inputs) if x.candidate.id == "st-eval-semi" and x.strategy == "short_let" and x.product.code == "HL5-75")
        a = r.assessment
        assert a is not None
        loan = Decimal("285000") * Decimal("0.75")
        assert a.loan == loan
        # cash in: 71,250 + SDLT 18,500 + 0 + 1,800 + 600 + 0 + 12,000
        assert a.cash_in.total == Decimal("104150.00")

        # The model rounds half-up to the penny at every line; so does this check.
        def p(x: Decimal) -> Decimal:
            return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # gross: 242/1.30 × 365 × 0.57
        gross = p(Decimal("242") / Decimal("1.30") * Decimal("365") * Decimal("0.57"))
        assert a.year.gross_income == gross
        # interest 6.19% interest-only: 213,750 × 0.0619 = 13,231.125 → 13,231.13 (half-up, not banker's)
        assert a.year.mortgage_interest == p(loan * Decimal("0.0619")) == Decimal("13231.13")
        # operating: 18% + 3% + cleaning 80×52 + utilities 2,160 + 10% + 600 + council tax 4,030.32 + estate 480
        stays = (Decimal("365") * Decimal("0.57") / 4).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        op = (
            p(gross * Decimal("0.18"))
            + p(gross * Decimal("0.03"))
            + 80 * stays
            + 2160
            + p(gross * Decimal("0.10"))
            + 600
            + Decimal("4030.32")
            + 480
        )
        assert a.year.operating_costs == op
        assert a.year.pre_tax_cash_flow == gross - op - a.year.mortgage_interest
        assert comparables(inputs)["st-eval-semi"].verdict == "within range"

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from property_assessor.lenders import assess, load_lender, required_rent_monthly, stress_rate
from property_assessor.models import Applicant, Candidate
from property_assessor.sourced import Sourced

CONFIG = Path(__file__).resolve().parents[1] / "config" / "lenders"


def stated(value: str) -> Sourced[Decimal]:
    return Sourced(value=Decimal(value), basis="stated", source="test", as_of=date(2026, 10, 16))


@pytest.fixture
def skipton():
    return load_lender(CONFIG / "skipton.yaml")


@pytest.fixture
def leeds():
    return load_lender(CONFIG / "leeds.yaml")


@pytest.fixture
def basic_applicant():
    return Applicant(tax_band="basic", gross_earned_income=stated("45000"))


def candidate(price: str, **kw) -> Candidate:
    base = dict(
        id="x",
        label="x",
        listing_url="https://example.invalid",
        postcode_district="TR16",
        town="Redruth",
        asking_price=stated(price),
        bedrooms=2,
        property_type="bungalow",
        tenure="freehold",
    )
    base.update(kw)
    return Candidate(**base)


def test_stress_rate_is_the_higher_of_rate_plus_uplift_or_floor(skipton):
    trk = skipton.product("TRK2")  # 4.44% tracker → +2% = 6.44% > 5.5%
    assert stress_rate(skipton, trk) == Decimal("0.0644")
    # a hypothetical cheap long fix is floored at 5.5%
    cheap = trk.model_copy(update={"kind": "fixed", "term_years": 5, "rate": stated("0.0400")})
    assert stress_rate(skipton, cheap) == Decimal("0.055")
    # an unconfirmed-term fix gets the short uplift (the harsher case)
    assert stress_rate(skipton, skipton.product("R2606")) == Decimal("0.0744")


def test_required_rent_matches_hand_arithmetic(skipton, basic_applicant):
    # £375,000 loan at 6.44% stress = £24,150 interest; ×125% / 12 = £2,515.63
    required, rate = required_rent_monthly(skipton, skipton.product("TRK2"), basic_applicant, Decimal("375000"))
    assert rate == Decimal("0.0644")
    assert required == Decimal("2515.63")
    higher = basic_applicant.model_copy(update={"tax_band": "higher"})
    assert required_rent_monthly(skipton, skipton.product("TRK2"), higher, Decimal("375000"))[0] == Decimal("2918.13")


def test_redruth_bungalow_fails_skipton_cover_by_a_wide_margin(skipton, basic_applicant):
    e = assess(skipton, skipton.product("TRK2"), candidate("500000"), basic_applicant, "long_let", stated("1100"))
    assert e.loan == Decimal("375000.00")
    assert e.ltv == Decimal("0.75")
    assert not e.eligible
    assert [c.name for c in e.failures] == ["rental cover"]
    assert "FAILS by £1,416" in e.failures[0].detail


def test_new_build_with_incentive_not_ruled_out_caps_ltv_at_70(skipton, basic_applicant):
    e = assess(
        skipton,
        skipton.product("TRK2"),
        candidate("500000", new_build=True),
        basic_applicant,
        "long_let",
        stated("3000"),
    )
    ltv = next(c for c in e.checks if c.name == "LTV")
    assert not ltv.passed
    assert "70%" in ltv.detail and "incentive" in ltv.detail
    # once the incentive is ruled out, 75% applies
    e2 = assess(
        skipton,
        skipton.product("TRK2"),
        candidate("500000", new_build=True, new_build_incentive=False),
        basic_applicant,
        "long_let",
        stated("3000"),
    )
    assert next(c for c in e2.checks if c.name == "LTV").passed


def test_skipton_rejects_short_let_outright(skipton, basic_applicant):
    e = assess(skipton, skipton.product("TRK2"), candidate("285000"), basic_applicant, "short_let", stated("5000"))
    use = next(c for c in e.checks if c.name == "property use")
    assert not use.passed and "NOT" in use.detail


def test_skipton_rejects_limited_company(skipton):
    ltd = Applicant(tax_band="basic", gross_earned_income=stated("45000"), limited_company=True)
    e = assess(skipton, skipton.product("TRK2"), candidate("285000"), ltd, "long_let", stated("2000"))
    assert not next(c for c in e.checks if c.name == "limited company").passed


def test_minimum_income_and_value(skipton):
    poor = Applicant(tax_band="basic", gross_earned_income=stated("15000"))
    e = assess(skipton, skipton.product("TRK2"), candidate("70000"), poor, "long_let", stated("2000"))
    assert {c.name for c in e.failures} == {"minimum income", "minimum property value"}


def test_missing_rent_fails_cover_rather_than_passing_silently(skipton, basic_applicant):
    e = assess(skipton, skipton.product("TRK2"), candidate("285000"), basic_applicant, "long_let", None)
    cover = next(c for c in e.checks if c.name == "rental cover")
    assert not cover.passed and "no rent figure" in cover.detail
    assert e.available_rent_monthly is None


def test_provenance_is_the_weakest_input(skipton, basic_applicant):
    e = assess(skipton, skipton.product("TRK2"), candidate("285000"), basic_applicant, "long_let", stated("1300"))
    assert e.provenance.basis == "stated"
    est = Sourced(value=Decimal("1300"), basis="estimate", source="test")
    assert assess(skipton, skipton.product("TRK2"), candidate("285000"), basic_applicant, "long_let", est).provenance.basis == "estimate"


def test_leeds_accepts_short_let_and_needs_40k_income(leeds, basic_applicant):
    e = assess(
        leeds,
        leeds.product("HL2-75"),
        candidate("285000", town="St Eval"),
        basic_applicant,
        "short_let",
        stated("3000"),
    )
    assert next(c for c in e.checks if c.name == "property use").passed
    assert next(c for c in e.checks if c.name == "minimum income").passed  # 45k ≥ 40k
    low = basic_applicant.model_copy(update={"gross_earned_income": stated("30000")})
    assert not next(
        c
        for c in assess(leeds, leeds.product("HL2-75"), candidate("285000"), low, "short_let", stated("3000")).checks
        if c.name == "minimum income"
    ).passed

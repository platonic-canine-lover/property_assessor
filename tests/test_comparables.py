from datetime import date
from decimal import Decimal
from pathlib import Path

from property_assessor.assessor import Inputs, comparables
from property_assessor.comparables import Sale, SalesSet, bedroom_score, compare, recency_score, type_score, weighted_quantile
from property_assessor.models import Candidate
from property_assessor.sourced import Sourced

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 16)


def cand(**kw) -> Candidate:
    base = dict(
        id="c",
        label="c",
        listing_url="https://example.invalid",
        postcode_district="TR16",
        town="Redruth",
        asking_price=Sourced(value=Decimal("300000"), basis="stated", source="t"),
        bedrooms=2,
        property_type="bungalow",
        tenure="freehold",
    )
    base.update(kw)
    return Candidate(**base)


def sale(price, d="2026-07-01", t="detached", beds=2, pc="TR16 4QG"):
    return Sale(price=Decimal(price), date=date.fromisoformat(d), address=f"x {pc}", postcode=pc, type=t, beds=beds)


def test_scores():
    assert type_score("bungalow", "detached") == Decimal("0.9")
    assert type_score("detached", "bungalow") == Decimal("0.9")
    assert type_score("semi_detached", "terraced") == Decimal("0.6")
    assert type_score("terraced", "flat") == Decimal("0.1")
    assert bedroom_score(3, 3) == Decimal("1.0") and bedroom_score(3, None) == Decimal("0.4") and bedroom_score(3, 6) == Decimal("0.05")
    assert recency_score(date(2026, 6, 1), TODAY) == Decimal("1.0")
    assert recency_score(date(2024, 6, 1), TODAY) == Decimal("0.3")
    mid = recency_score(date(2025, 7, 16), TODAY)  # 15 months: 1 − 9/18 × 0.7 = 0.65
    assert Decimal("0.64") < mid < Decimal("0.66")


def test_weighted_quantile():
    pairs = [(Decimal(100), Decimal(1)), (Decimal(200), Decimal(1)), (Decimal(300), Decimal(2))]
    assert weighted_quantile(pairs, Decimal("0.5")) == Decimal(200)
    assert weighted_quantile(pairs, Decimal("0.75")) == Decimal(300)
    assert weighted_quantile(pairs, Decimal("0.25")) == Decimal(100)


def test_same_sector_outweighs_same_district_and_verdict_uses_the_interquartile_range():
    sales = SalesSet(
        source="s",
        as_of=date(2026, 7, 31),
        basis="published",
        sales=[
            sale(250000, pc="TR16 4AA"),
            sale(260000, pc="TR16 4AB"),
            sale(270000, pc="TR16 5ZZ"),
            sale(280000, pc="TR16 5ZY"),
            sale(290000, pc="TR16 4AC"),
            sale(400000, pc="TR16 6AA", t="detached", beds=4),
        ],
    )
    c = compare(cand(postcode="TR16 4AQ"), sales, TODAY)
    top = c.scored[0]
    assert top.locality_score == Decimal("1.0") and top.sale.postcode.startswith("TR16 4")
    assert c.verdict in ("within range", "above range", "below range")
    assert c.weighted_q1 is not None and c.weighted_q1 <= c.weighted_median <= c.weighted_q3
    # a candidate with no full postcode gets 0.7 locality on every sale
    c2 = compare(cand(), sales, TODAY)
    assert all(s.locality_score == Decimal("0.7") for s in c2.scored)


def test_thin_sets_withhold_the_verdict():
    sales = SalesSet(source="s", as_of=date(2026, 7, 31), basis="published", sales=[sale(250000), sale(260000)])
    c = compare(cand(), sales, TODAY)
    assert c.verdict == "too few comparables" and c.weighted_median is None
    assert c.effective_n < Decimal("3")


def test_worked_example_verdicts():
    inputs = Inputs.load(
        ROOT / "config",
        ROOT / "data/candidates.yaml",
        ROOT / "data/applicant.yaml",
        ROOT / "data/snapshots/cornwall-2026-10.yaml",
        "cornwall",
        today=TODAY,
    )
    comps = comparables(inputs)
    redruth, st_eval = comps["redruth-bungalow"], comps["st-eval-semi"]
    assert redruth.verdict == "above range" and redruth.premium_over_median > Decimal("0.5")
    assert redruth.scored[0].sale.type == "detached" and redruth.scored[0].type_score == Decimal("0.9")
    assert st_eval.verdict == "within range"
    assert st_eval.scored[0].sale.address.startswith("96 Talmena Avenue")  # the one 3-bed semi scores highest
    for c in comps.values():
        assert c.source.startswith("https://") and c.basis == "published"

"""Weighted comparable sales: is the asking price in line with what actually sold?

The one idea worth taking from the generic AVM playbook. No model is
trained; each recorded sale is scored for how comparable it is to the
candidate and the scores are used as weights:

    similarity = type_score × bedroom_score × recency_score × locality_score

    type_score      1.0 same type; 0.9 bungalow vs detached (a detached bungalow
                    is detached); 0.6 other house-to-house pairs; 0.1 house vs flat
    bedroom_score   1.0 same bedrooms; 0.6 one apart; 0.25 two apart; 0.05 more
                    (unknown bedrooms: 0.4, so the sale counts but not much)
    recency_score   1.0 inside 6 months, falling linearly to 0.3 at 24 months
    locality_score  1.0 same postcode sector; 0.7 same district, different or
                    unknown sector; 0.4 sale postcode unknown; 0.1 different
                    district (we do not have coordinates: listings give the
                    district, Price Paid rows give the full postcode)

Output: the weighted median sale price, a weighted interquartile range, the
effective number of comparables (sum of weights), and the asking price's
position against that range. With fewer than about three effective
comparables the result is marked thin and the asking-price verdict is
withheld. Every sale in the output carries its source.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from property_assessor.models import Candidate, PropertyType
from property_assessor.sourced import Basis

MONEY = Decimal("0.01")


class Sale(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: Decimal
    date: date
    address: str
    postcode: str | None = None
    type: PropertyType
    beds: int | None = None


class SalesSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    as_of: date
    basis: Basis
    note: str | None = None
    sales: list[Sale]


class ScoredSale(BaseModel):
    sale: Sale
    weight: Decimal
    type_score: Decimal
    bedroom_score: Decimal
    recency_score: Decimal
    locality_score: Decimal


Verdict = Literal["within range", "above range", "below range", "too few comparables"]


class ComparablesResult(BaseModel):
    candidate_id: str
    asking_price: Decimal
    weighted_median: Decimal | None
    weighted_q1: Decimal | None
    weighted_q3: Decimal | None
    effective_n: Decimal
    verdict: Verdict
    premium_over_median: Decimal | None = Field(default=None, description="asking ÷ weighted median − 1")
    scored: list[ScoredSale]
    source: str
    as_of: date
    basis: Basis
    note: str | None = None


HOUSE_TYPES: set[PropertyType] = {"detached", "semi_detached", "terraced", "bungalow"}


def type_score(candidate: PropertyType, sale: PropertyType) -> Decimal:
    if candidate == sale:
        return Decimal("1.0")
    if {candidate, sale} == {"bungalow", "detached"}:
        return Decimal("0.9")
    if candidate in HOUSE_TYPES and sale in HOUSE_TYPES:
        return Decimal("0.6")
    return Decimal("0.1")


def bedroom_score(candidate: int, sale: int | None) -> Decimal:
    if sale is None:
        return Decimal("0.4")
    d = abs(candidate - sale)
    return {0: Decimal("1.0"), 1: Decimal("0.6"), 2: Decimal("0.25")}.get(d, Decimal("0.05"))


def recency_score(sale_date: date, today: date) -> Decimal:
    months = Decimal((today - sale_date).days) / Decimal("30.44")
    if months <= 6:
        return Decimal("1.0")
    if months >= 24:
        return Decimal("0.3")
    return (Decimal("1.0") - (months - 6) / 18 * Decimal("0.7")).quantize(Decimal("0.001"))


def _sector(postcode: str | None) -> str | None:
    if not postcode:
        return None
    parts = postcode.strip().upper().split()
    if len(parts) != 2 or not parts[1]:
        return None
    return f"{parts[0]} {parts[1][0]}"


def _district(postcode: str | None) -> str | None:
    if not postcode:
        return None
    return postcode.strip().upper().split()[0] or None


def locality_score(candidate: Candidate, sale: Sale) -> Decimal:
    sale_district = _district(sale.postcode)
    if sale_district is None:
        return Decimal("0.4")
    if sale_district != candidate.postcode_district.upper():
        return Decimal("0.1")
    cs, ss = _sector(candidate.postcode), _sector(sale.postcode)
    if cs and ss and cs == ss:
        return Decimal("1.0")
    return Decimal("0.7")


def weighted_quantile(pairs: list[tuple[Decimal, Decimal]], q: Decimal) -> Decimal:
    """pairs: (value, weight). Value at which cumulative weight first reaches q × total."""
    ordered = sorted(pairs, key=lambda p: p[0])
    total = sum((w for _, w in ordered), Decimal(0))
    target = total * q
    running = Decimal(0)
    for value, w in ordered:
        running += w
        if running >= target:
            return value
    return ordered[-1][0]


def compare(candidate: Candidate, sales: SalesSet, today: date, thin_below: Decimal = Decimal("3")) -> ComparablesResult:
    scored: list[ScoredSale] = []
    for sale in sales.sales:
        ts = type_score(candidate.property_type, sale.type)
        bs = bedroom_score(candidate.bedrooms, sale.beds)
        rs = recency_score(sale.date, today)
        ls = locality_score(candidate, sale)
        scored.append(
            ScoredSale(
                sale=sale,
                weight=(ts * bs * rs * ls).quantize(Decimal("0.0001")),
                type_score=ts,
                bedroom_score=bs,
                recency_score=rs,
                locality_score=ls,
            )
        )
    scored.sort(key=lambda s: s.weight, reverse=True)
    effective_n = sum((s.weight for s in scored), Decimal(0)).quantize(Decimal("0.01"))
    asking = candidate.asking_price.value

    if effective_n < thin_below or not scored:
        return ComparablesResult(
            candidate_id=candidate.id,
            asking_price=asking,
            weighted_median=None,
            weighted_q1=None,
            weighted_q3=None,
            effective_n=effective_n,
            verdict="too few comparables",
            scored=scored,
            source=sales.source,
            as_of=sales.as_of,
            basis=sales.basis,
            note=sales.note,
        )

    pairs = [(s.sale.price, s.weight) for s in scored]
    median = weighted_quantile(pairs, Decimal("0.5"))
    q1 = weighted_quantile(pairs, Decimal("0.25"))
    q3 = weighted_quantile(pairs, Decimal("0.75"))
    verdict: Verdict = "within range" if q1 <= asking <= q3 else ("above range" if asking > q3 else "below range")
    premium = ((asking / median) - 1).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return ComparablesResult(
        candidate_id=candidate.id,
        asking_price=asking,
        weighted_median=median,
        weighted_q1=q1,
        weighted_q3=q3,
        effective_n=effective_n,
        verdict=verdict,
        premium_over_median=premium,
        scored=scored,
        source=sales.source,
        as_of=sales.as_of,
        basis=sales.basis,
        note=sales.note,
    )


def load_sales(snapshot: Path) -> dict[str, SalesSet]:
    """The `comparables:` section of a market snapshot, keyed by its set name."""
    raw = yaml.safe_load(snapshot.read_text())
    return {k: SalesSet.model_validate(v) for k, v in (raw.get("comparables") or {}).items()}


def sales_for(candidate: Candidate, sets: dict[str, SalesSet]) -> SalesSet | None:
    """Pick the comparables set whose name starts with the candidate's postcode district."""
    for name, s in sets.items():
        if name.upper().startswith(candidate.postcode_district.upper() + "_"):
            return s
    return None

"""Every number that enters a calculation says where it came from.

A `Sourced` value carries its basis and provenance; the report prints both
next to any figure it affects. The bases, in descending order of trust:

    published   a figure printed by the party that owns it (a lender's rate
                card, a council's web page, an ONS table) on the date given
    stated      a figure given by the user (an asking price from a listing,
                a product chosen in a product finder)
    measured    something observed directly
    estimate    derived from published figures by a stated method
    assumption  a round number somebody chose, with a reason

An `assumption` is allowed, but it is never silently promoted: the report
marks the overall basis of a result by its weakest input.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

Basis = Literal["published", "stated", "measured", "estimate", "assumption"]

BASIS_RANK: dict[Basis, int] = {"published": 0, "stated": 1, "measured": 2, "estimate": 3, "assumption": 4}

T = TypeVar("T", Decimal, int, str, bool)


class Sourced(BaseModel, Generic[T]):
    model_config = ConfigDict(frozen=True)

    value: T
    basis: Basis
    source: str = Field(min_length=1, description="URL, document title, or who said it")
    as_of: date | None = None
    note: str | None = None

    def __str__(self) -> str:
        when = f" {self.as_of.isoformat()}" if self.as_of else ""
        return f"{self.value} [{self.basis}{when}]"


def weakest(*items: Sourced[Any] | None) -> Basis:
    """The least trustworthy basis among the inputs; what a derived figure inherits."""
    present = [i for i in items if i is not None]
    if not present:
        return "assumption"
    return max((i.basis for i in present), key=lambda b: BASIS_RANK[b])


class Provenance(BaseModel):
    """Attached to any derived figure: its basis and the inputs it rests on."""

    basis: Basis
    inputs: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, **named: Sourced[Any] | None) -> Provenance:
        inputs = [f"{k}={v}" for k, v in named.items() if v is not None]
        return cls(basis=weakest(*named.values()), inputs=inputs)

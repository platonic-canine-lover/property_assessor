"""Domain models. Money is Decimal pounds; percentages are Decimal fractions (0.05 = 5%)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from property_assessor.sourced import Sourced

Strategy = Literal["long_let", "short_let"]
TaxBand = Literal["basic", "higher"]
PropertyType = Literal["detached", "semi_detached", "terraced", "flat", "bungalow"]
Tenure = Literal["freehold", "leasehold"]


class Candidate(BaseModel):
    """One property under consideration, as the listing describes it."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    listing_url: str
    postcode_district: str = Field(pattern=r"^[A-Z]{1,2}\d{1,2}[A-Z]?$")
    postcode: str | None = Field(default=None, description="Full postcode if known; listings usually omit it")
    town: str = Field(description="Used to pick the short-let market figures")
    asking_price: Sourced[Decimal]
    bedrooms: int = Field(ge=0)
    bathrooms: int | None = None
    property_type: PropertyType
    tenure: Tenure
    council_tax_band: str | None = None
    epc_rating: str | None = None
    new_build: bool = False
    new_build_incentive: bool | None = Field(default=None, description="None = not stated; lenders cap LTV if a developer incentive exists")
    estate_charge_monthly: Sourced[Decimal] | None = None
    private_water_or_drainage: bool | None = None
    holiday_let_restriction: Literal["none_stated", "restricted", "permitted"] = "none_stated"
    notes: list[str] = Field(default_factory=list)


class Applicant(BaseModel):
    model_config = ConfigDict(frozen=True)

    tax_band: TaxBand
    gross_earned_income: Sourced[Decimal]
    owns_other_residential_property: bool = True
    uk_resident: bool = True
    limited_company: bool = False
    portfolio_landlord: bool = False
    deposit_fraction: Decimal = Field(default=Decimal("0.25"), gt=0, lt=1)
    interest_only: bool = True

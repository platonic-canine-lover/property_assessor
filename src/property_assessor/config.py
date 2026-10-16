"""Typed loaders for the YAML config files."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from property_assessor.models import TaxBand
from property_assessor.sourced import Basis, Sourced


class SdltBand(BaseModel):
    up_to: int | None
    rate: Decimal


class Sdlt(BaseModel):
    source: str
    as_of: date
    basis: Basis
    bands: list[SdltBand]
    additional_property_surcharge: Decimal
    non_resident_surcharge: Decimal


class IncomeTax(BaseModel):
    source: str
    as_of: date
    basis: Basis
    marginal_rate: dict[TaxBand, Decimal]
    finance_cost_credit: Decimal
    finance_cost_credit_source: str


class FhlNote(BaseModel):
    note: str
    source: str
    basis: Basis
    as_of: date


class TaxConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    sdlt: Sdlt
    income_tax: IncomeTax
    furnished_holiday_let: FhlNote


class CouncilTax(BaseModel):
    source: str
    as_of: date
    basis: Basis
    note: str | None = None
    annual_by_band: dict[str, Decimal | None]


class Premium(BaseModel):
    source: str
    as_of: date
    basis: Basis
    multiplier: Decimal


class Sbrr(BaseModel):
    note: str
    basis: Basis
    source: str
    annual_charge_if_rated: Decimal


class BusinessRates(BaseModel):
    source: str
    as_of: date
    basis: Basis
    available_nights_last_12m: int
    available_nights_next_12m: int
    let_nights_last_12m: int
    note: str | None = None
    small_business_rate_relief: Sbrr


class AreaRules(BaseModel):
    model_config = ConfigDict(frozen=True)
    council_tax: CouncilTax
    second_home_premium: Premium
    business_rates_instead_of_council_tax: BusinessRates


SDec = Sourced[Decimal]


class PurchaseCosts(BaseModel):
    legal_fees: SDec
    survey: SDec
    mortgage_valuation: SDec


class LongLetCosts(BaseModel):
    management_fee_fraction: SDec
    void_weeks_per_year: SDec
    maintenance_fraction: SDec
    landlord_insurance: SDec
    tenant_pays_council_tax: bool
    tenant_pays_utilities: bool
    safety_certificates: SDec


class ShortLetCosts(BaseModel):
    management_fee_fraction: SDec
    platform_fee_fraction: SDec
    cleaning_per_stay: SDec
    average_stay_nights: SDec
    utilities_monthly: SDec
    maintenance_fraction: SDec
    insurance: SDec
    furnishing_one_off: SDec
    owner_pays_council_tax: bool


class Fx(BaseModel):
    usd_per_gbp: SDec


class CostConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    purchase: PurchaseCosts
    long_let: LongLetCosts
    short_let: ShortLetCosts
    fx: Fx


class MarketFigure(BaseModel):
    """A market input for one town / area and bedroom count."""

    model_config = ConfigDict(frozen=True)
    area: str
    bedrooms: int | None = None
    granularity: Literal["postcode", "postcode_sector", "postcode_district", "town", "local_authority", "county"]
    value: SDec


class Market(BaseModel):
    """Snapshotted market figures: long-let rents, short-let ADR and occupancy, sold comparables."""

    model_config = ConfigDict(frozen=True)
    long_let_rent_monthly: list[MarketFigure] = Field(default_factory=list)
    short_let_adr_usd: list[MarketFigure] = Field(default_factory=list)
    short_let_occupancy: list[MarketFigure] = Field(default_factory=list)
    sold_median: list[MarketFigure] = Field(default_factory=list)

    @staticmethod
    def _pick(figures: list[MarketFigure], area_keys: list[str], bedrooms: int | None) -> MarketFigure | None:
        """Most specific match: first area key that matches, preferring an exact bedroom count."""
        for key in area_keys:
            exact = [f for f in figures if f.area == key and f.bedrooms == bedrooms]
            if exact:
                return exact[0]
            any_bed = [f for f in figures if f.area == key and f.bedrooms is None]
            if any_bed:
                return any_bed[0]
        return None

    def rent(self, area_keys: list[str], bedrooms: int) -> MarketFigure | None:
        return self._pick(self.long_let_rent_monthly, area_keys, bedrooms)

    def adr_usd(self, area_keys: list[str], bedrooms: int) -> MarketFigure | None:
        return self._pick(self.short_let_adr_usd, area_keys, bedrooms)

    def occupancy(self, area_keys: list[str], bedrooms: int) -> MarketFigure | None:
        return self._pick(self.short_let_occupancy, area_keys, bedrooms)

    def sold(self, area_keys: list[str], bedrooms: int) -> MarketFigure | None:
        return self._pick(self.sold_median, area_keys, bedrooms)


def _load(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def load_tax(path: Path) -> TaxConfig:
    return TaxConfig.model_validate(_load(path))


def load_area_rules(path: Path) -> AreaRules:
    return AreaRules.model_validate(_load(path))


def load_costs(path: Path) -> CostConfig:
    return CostConfig.model_validate(_load(path))


def load_market(path: Path) -> Market:
    return Market.model_validate(_load(path))

"""Greenhouse-gas accounting (tonnes CO2e).

Scope 3, Category 1 (purchased goods): spend-based.
    PO spend × EPA "Supply Chain Greenhouse Gas Emission Factors v1.3 by NAICS-6"
    (2017 NAICS, kg CO2e per 2022 USD at purchaser price, factor *with* margins),
    × the vendor's emission intensity index (1.0 = industry average).
    Source: https://catalog.data.gov/dataset/supply-chain-greenhouse-gas-emission-factors-v1-3-by-naics-6
    Spend is not deflated to 2022 dollars, so Scope 3 is slightly overstated for later years.

Scope 1 (fuel) and Scope 2 (electricity, location-based): activity data ×
    EPA GHG Emission Factors Hub, January 2025 (Tables 1, 2, 6; GWPs from Table 11, AR5).
    https://www.epa.gov/climateleadership/ghg-emission-factors-hub
    Mobile combustion counts CO2 only (CH4/N2O depend on vehicle type and are <1%).
"""

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib.resources import files

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .analytics import month_share
from .models import EnergyUsage, PurchaseOrder, Vendor

EPA_SUPPLY_CHAIN_CSV = "epa_v1.3_naics_co2e_usd2022.csv"

GWP_CH4 = 28
GWP_N2O = 265
_LB_TO_KG = 0.45359237

# kg CO2e per unit of activity.
ELECTRICITY_KG_PER_KWH = (771.5 + 0.057 * GWP_CH4 + 0.008 * GWP_N2O) * _LB_TO_KG / 1000  # eGRID2023 US average
NATURAL_GAS_KG_PER_THERM = 0.1 * (53.06 + (1.0 * GWP_CH4 + 0.10 * GWP_N2O) / 1000)  # 1 therm = 0.1 mmBtu
DIESEL_KG_PER_GALLON = 10.21
GASOLINE_KG_PER_GALLON = 8.78

SOURCES = {
    "scope3": "EPA Supply Chain GHG Emission Factors v1.3 (NAICS-6, 2022 USD, with margins)",
    "scope1_2": "EPA GHG Emission Factors Hub (January 2025); electricity: eGRID2023 US average",
}


@dataclass(frozen=True)
class IndustryFactor:
    naics_code: str
    title: str
    kg_co2e_per_usd: float


@cache
def epa_factors() -> dict[str, IndustryFactor]:
    path = files("prosight") / "data" / EPA_SUPPLY_CHAIN_CSV
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {
            row["2017 NAICS Code"]: IndustryFactor(
                row["2017 NAICS Code"],
                row["2017 NAICS Title"],
                float(row["Supply Chain Emission Factors with Margins"]),
            )
            for row in csv.DictReader(f)
        }


def industry_factor(naics_code: str) -> IndustryFactor:
    try:
        return epa_factors()[naics_code]
    except KeyError:
        raise ValueError(f"NAICS code {naics_code} is not in the EPA factor set.") from None


def vendor_factor(vendor: Vendor) -> float:
    """kg CO2e per USD spent with this vendor."""
    return industry_factor(vendor.naics_code).kg_co2e_per_usd * vendor.emission_intensity_index


@dataclass
class EmissionsSummary:
    start: date
    end: date
    scope1: float
    scope2: float
    scope3: float
    purchases_usd: float
    electricity_kwh: float
    natural_gas_therms: float
    diesel_gallons: float
    gasoline_gallons: float
    scope3_by_vendor: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.scope1 + self.scope2 + self.scope3


def emissions_summary(db: Session, start: date, end: date) -> EmissionsSummary:
    pos = db.scalars(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.vendor))
        .where(PurchaseOrder.issued_date.between(start, end), PurchaseOrder.status != "cancelled")
    )
    by_vendor: dict[str, float] = defaultdict(float)
    purchases = 0.0
    for po in pos:
        purchases += po.amount
        by_vendor[po.vendor.name] += po.amount * vendor_factor(po.vendor) / 1000

    kwh = therms = diesel = gasoline = 0.0
    for row in db.scalars(select(EnergyUsage).where(EnergyUsage.month <= end, EnergyUsage.month >= start.replace(day=1))):
        share = month_share(row.month, start, end)
        kwh += row.electricity_kwh * share
        therms += row.natural_gas_therms * share
        diesel += row.diesel_gallons * share
        gasoline += row.gasoline_gallons * share

    return EmissionsSummary(
        start=start,
        end=end,
        scope1=(therms * NATURAL_GAS_KG_PER_THERM + diesel * DIESEL_KG_PER_GALLON + gasoline * GASOLINE_KG_PER_GALLON) / 1000,
        scope2=kwh * ELECTRICITY_KG_PER_KWH / 1000,
        scope3=sum(by_vendor.values()),
        purchases_usd=purchases,
        electricity_kwh=kwh,
        natural_gas_therms=therms,
        diesel_gallons=diesel,
        gasoline_gallons=gasoline,
        scope3_by_vendor=dict(by_vendor),
    )

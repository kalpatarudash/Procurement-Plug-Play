"""Deterministic synthetic dataset standing in for an ERP's history.

Two years of weekly sales per SKU (with seasonality, growth, a recent Spring Outerwear slowdown
and slow-moving Accessories), FIFO stock lots, monthly operating expenses, and the PO history.
Scaled so fiscal-year-to-date revenue matches the UI's $4.82M.

Run:  uv run python -m prosight.seed      (drops and recreates all tables)
"""

import math
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .analytics import add_months, fiscal_year_start
from .config import settings
from .db import Base, SessionLocal, engine
from .models import (
    Category,
    ConnectorState,
    InventoryLot,
    EnergyUsage,
    OperatingExpense,
    PurchaseOrder,
    SalesWeek,
    Sku,
    Vendor,
)

TARGET_FYTD_REVENUE = 4_820_000
TARGET_FYTD_OPEX = 1_298_000
TARGET_FYTD_TAXES = 158_000
HISTORY_WEEKS = 104

VENDORS = [
    # name, on-time, lead days, price index, 2017 NAICS, emission intensity vs. industry average
    ("Northstar Textiles", 0.96, 21, 1.07, "315280", 1.00),  # Other Cut and Sew Apparel Mfg
    ("Evergreen Manufacturing", 0.91, 28, 1.02, "315280", 0.82),
    ("Atlas Apparel Co.", 0.88, 18, 0.97, "315990", 1.15),  # Apparel Accessories
    ("Stride Footwear Ltd.", 0.94, 35, 1.00, "316210", 0.95),  # Footwear Mfg
    ("Hearth & Home Supply", 0.90, 24, 0.99, "314120", 0.90),  # Curtain and Linen Mills
]

# Typical month of energy use across warehouse and stores (Scope 1 and 2 activity data).
ENERGY_BASE = {"electricity_kwh": 100_000, "natural_gas_therms": 3_500, "diesel_gallons": 1_300, "gasoline_gallons": 400}

CATEGORIES = [
    # name, revenue share, price range, gross margin, peak week, seasonal amplitude,
    # annual growth, target weeks of cover, aging stock?, vendors, SKU names
    ("Spring Outerwear", 0.30, (89, 219), 0.45, 12, 0.5, 0.10, 19, False,
     ["Northstar Textiles", "Evergreen Manufacturing", "Atlas Apparel Co."],
     ["Rain Shell Jacket", "Quilted Liner Vest", "Trench Coat", "Packable Windbreaker",
      "Fleece Overshirt", "Lightweight Parka", "Softshell Jacket", "Utility Field Jacket"]),
    ("Classic Footwear", 0.26, (69, 179), 0.42, 35, 0.2, 0.16, 7, False,
     ["Stride Footwear Ltd."],
     ["Leather Chelsea Boot", "Canvas Low-Top", "Suede Loafer", "Trail Runner",
      "Court Sneaker", "Derby Shoe", "Slip-On Mule", "Desert Boot"]),
    ("Accessories", 0.14, (19, 89), 0.50, 50, 0.35, -0.08, 34, True,
     ["Atlas Apparel Co.", "Northstar Textiles"],
     ["Wool Beanie", "Leather Belt", "Canvas Tote", "Silk Scarf",
      "Card Wallet", "Knit Gloves", "Bucket Hat", "Weekender Bag"]),
    ("Home & Lifestyle", 0.15, (24, 129), 0.46, 48, 0.3, 0.12, 9, False,
     ["Hearth & Home Supply", "Evergreen Manufacturing"],
     ["Linen Throw", "Ceramic Mug Set", "Cotton Bath Towel", "Scented Candle",
      "Waffle Robe", "Stoneware Bowl"]),
    ("Basics", 0.15, (18, 59), 0.38, 26, 0.1, 0.14, 6, False,
     ["Atlas Apparel Co.", "Evergreen Manufacturing"],
     ["Crew Tee", "Rib Tank", "Oxford Shirt", "Chino Short", "Jersey Polo", "Henley Tee"]),
]


def _week_starts(as_of: date) -> list[date]:
    last = as_of - timedelta(days=as_of.weekday() + 7)  # Monday of the last complete week
    return [last - timedelta(weeks=i) for i in reversed(range(HISTORY_WEEKS))]


def seed(db: Session, rng: random.Random | None = None) -> None:
    rng = rng or random.Random(490)
    as_of = settings.as_of
    weeks = _week_starts(as_of)
    fy_start = fiscal_year_start(as_of)

    vendors = {name: Vendor(name=name, on_time_rate=ot, lead_time_days=lt, price_index=pi, naics_code=n,
                            emission_intensity_index=ix)
               for name, ot, lt, pi, n, ix in VENDORS}
    db.add_all(vendors.values())

    # Pass 1: unrounded demand per SKU per week.
    weekly_target = TARGET_FYTD_REVENUE / 50
    plans = []  # (sku, demand list, cover weeks, aging flag)
    purchase_orders: list[PurchaseOrder] = []
    code_n = 1000
    for cat_name, share, (lo, hi), margin, peak, amp, growth, cover, aging, vendor_names, names in CATEGORIES:
        category = Category(name=cat_name)
        db.add(category)
        for name in names:
            code_n += 1
            price = round(rng.uniform(lo, hi), 2)
            sku = Sku(
                code=f"SKU-{code_n}",
                name=name,
                category=category,
                vendor=vendors[rng.choice(vendor_names)],
                unit_price=price,
                unit_cost=round(price * (1 - margin + rng.uniform(-0.04, 0.04)), 2),
            )
            base = share * weekly_target / len(names) / price * rng.uniform(0.6, 1.4)
            demand = []
            for i, week in enumerate(weeks):
                week_of_year = week.isocalendar().week
                season = 1 + amp * math.cos(2 * math.pi * (week_of_year - peak) / 52)
                trend = (1 + growth) ** ((i - HISTORY_WEEKS) / 52)
                slowdown = 0.93 ** (i - (HISTORY_WEEKS - 6)) if cat_name == "Spring Outerwear" and i >= HISTORY_WEEKS - 6 else 1
                demand.append(max(0.0, base * season * trend * slowdown * rng.gauss(1, 0.12)))
            plans.append((sku, demand, cover, aging))

    # Scale so fiscal-YTD revenue hits the target, then write whole units.
    fytd_revenue = sum(
        d * sku.unit_price for sku, demand, _, _ in plans for week, d in zip(weeks, demand) if week >= fy_start
    )
    scale = TARGET_FYTD_REVENUE / fytd_revenue
    for sku, demand, cover, aging in plans:
        for week, d in zip(weeks, demand):
            units = round(d * scale)
            db.add(SalesWeek(sku=sku, week_start=week, units=units,
                             revenue=round(units * sku.unit_price, 2), cogs=round(units * sku.unit_cost, 2)))
        recent_avg = sum(demand[-12:]) * scale / 12

        # Stock on hand as FIFO lots; aging categories carry a large old lot.
        on_hand = max(1, round(recent_avg * cover * rng.uniform(0.8, 1.2)))
        if aging:
            old = round(on_hand * rng.uniform(0.55, 0.85))
            db.add(InventoryLot(sku=sku, received_date=as_of - timedelta(days=rng.randint(200, 400)), quantity=old, unit_cost=sku.unit_cost))
            on_hand -= old
        splits = sorted(rng.sample(range(1, on_hand), k=min(2, on_hand - 1))) if on_hand > 2 else []
        bounds = [0, *splits, on_hand]
        for a, b in zip(bounds, bounds[1:]):
            db.add(InventoryLot(sku=sku, received_date=as_of - timedelta(days=rng.randint(5, 120)), quantity=b - a, unit_cost=sku.unit_cost))

        # PO history: roughly every 6 weeks, sized to 6 weeks of demand.
        vendor = sku.vendor
        issued = weeks[0] + timedelta(days=rng.randint(0, 41))
        while issued <= as_of:
            qty = max(1, round(recent_avg * 6 * rng.uniform(0.8, 1.2)))
            expected = issued + timedelta(days=vendor.lead_time_days)
            received = expected + timedelta(days=0 if rng.random() < vendor.on_time_rate else rng.randint(3, 14))
            purchase_orders.append(PurchaseOrder(
                vendor=vendor, sku=sku, quantity=qty,
                unit_cost=round(sku.unit_cost * vendor.price_index, 2), currency=settings.currency,
                status="received" if received <= as_of else "open", issued_date=issued, expected_date=expected,
                received_date=received if received <= as_of else None,
                external_system="demo",
            ))
            issued += timedelta(days=rng.randint(35, 49))

    # Monthly opex/taxes, growing ~8% a year, scaled to the FYTD targets.
    months = []
    m = add_months(weeks[0], 0)
    while m <= as_of:
        months.append(m)
        m = add_months(m, 1)
    raw = {month: (1.08 ** ((month - as_of).days / 365)) * rng.uniform(0.92, 1.08) for month in months}
    fytd_months = [month for month in months if month >= fy_start]
    # The current month only counts in proportion to the days elapsed.
    def fytd_weight(month: date) -> float:
        return (as_of.day / 30) if month == as_of.replace(day=1) else 1.0
    fytd_raw = sum(raw[month] * fytd_weight(month) for month in fytd_months)
    for month in months:
        db.add(OperatingExpense(month=month,
                                opex=round(raw[month] * TARGET_FYTD_OPEX / fytd_raw, 2),
                                taxes_interest=round(raw[month] * TARGET_FYTD_TAXES / fytd_raw, 2)))

    # Energy: electricity peaks in summer (cooling), gas in winter (heating), fleet tracks sales.
    for month in months:
        summer = math.cos(2 * math.pi * (month.month - 7) / 12)
        db.add(EnergyUsage(
            month=month,
            electricity_kwh=round(ENERGY_BASE["electricity_kwh"] * (1 + 0.2 * summer) * rng.uniform(0.95, 1.05)),
            natural_gas_therms=round(ENERGY_BASE["natural_gas_therms"] * (1 - 0.7 * summer) * rng.uniform(0.9, 1.1)),
            diesel_gallons=round(ENERGY_BASE["diesel_gallons"] * rng.uniform(0.9, 1.1)),
            gasoline_gallons=round(ENERGY_BASE["gasoline_gallons"] * rng.uniform(0.9, 1.1)),
        ))

    # Number POs chronologically.
    purchase_orders.sort(key=lambda o: (o.issued_date, o.sku.code))
    for i, po in enumerate(purchase_orders, start=1):
        po.number = po.external_ref = f"PO-{po.issued_date.year}-{i:04d}"
    db.add_all(purchase_orders)

    db.add(ConnectorState(id="demo", connected=True))
    db.commit()


def reset_and_seed() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)


if __name__ == "__main__":
    reset_and_seed()
    print(f"Seeded {settings.database_url}")

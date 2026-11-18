"""Financial and inventory figures computed from the stored data."""

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .connectors.base import FinancialSummary
from .models import OperatingExpense, PurchaseOrder, SalesWeek, Sku, Vendor

View = Literal["YTD", "QTD", "MTD"]


# --- Dates -----------------------------------------------------------------------------------
def add_months(d: date, n: int) -> date:
    """First day of the month `n` months after `d`'s month."""
    years, month0 = divmod(d.month - 1 + n, 12)
    return date(d.year + years, month0 + 1, 1)


def shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28)


def fiscal_year_start(d: date) -> date:
    start_month = settings.fiscal_year_start_month
    return date(d.year if d.month >= start_month else d.year - 1, start_month, 1)


def period_for_view(view: View, as_of: date) -> tuple[date, date]:
    if view == "MTD":
        return as_of.replace(day=1), as_of
    fy = fiscal_year_start(as_of)
    if view == "QTD":
        months_in = (as_of.year - fy.year) * 12 + as_of.month - fy.month
        return add_months(fy, months_in // 3 * 3), as_of
    return fy, as_of


# --- Financials ------------------------------------------------------------------------------
def month_share(month: date, start: date, end: date) -> float:
    """Fraction of `month` (a first-of-month date) that falls inside [start, end]."""
    month_days = calendar.monthrange(month.year, month.month)[1]
    overlap = (min(end, month.replace(day=month_days)) - max(start, month)).days + 1
    return max(0, overlap) / month_days


def _prorated_expenses(db: Session, start: date, end: date) -> tuple[float, float]:
    rows = db.scalars(
        select(OperatingExpense).where(OperatingExpense.month <= end, OperatingExpense.month >= start.replace(day=1))
    )
    opex = taxes = 0.0
    for row in rows:
        share = month_share(row.month, start, end)
        opex += row.opex * share
        taxes += row.taxes_interest * share
    return opex, taxes


def financial_summary(db: Session, start: date, end: date) -> FinancialSummary:
    revenue, cogs = db.execute(
        select(func.coalesce(func.sum(SalesWeek.revenue), 0.0), func.coalesce(func.sum(SalesWeek.cogs), 0.0)).where(
            SalesWeek.week_start.between(start, end)
        )
    ).one()
    opex, taxes = _prorated_expenses(db, start, end)
    return FinancialSummary(start, end, settings.currency, revenue, cogs, opex, taxes)


def monthly_revenue(db: Session, start: date, end: date) -> list[tuple[date, float]]:
    """Revenue by calendar month. Each week's revenue is spread evenly over its seven days, so a
    month gets exactly its own days (bucketing whole weeks by start date makes months with five
    week-starts look like spikes)."""
    totals: dict[date, float] = defaultdict(float)
    for week_start, revenue in db.execute(
        select(SalesWeek.week_start, SalesWeek.revenue).where(SalesWeek.week_start.between(start, end))
    ):
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            totals[day.replace(day=1)] += revenue / 7
    return sorted(totals.items())


# --- Suppliers -------------------------------------------------------------------------------
def category_suppliers(db: Session) -> dict[int, dict[int, Vendor]]:
    """Vendors able to supply each category: preferred vendors in the item master plus every vendor
    the category has been ordered from. The PO recommender chooses among exactly these."""
    suppliers: dict[int, dict[int, Vendor]] = defaultdict(dict)
    for sku in db.scalars(select(Sku).options(selectinload(Sku.vendor))):
        suppliers[sku.category_id][sku.vendor.id] = sku.vendor
    for po in db.scalars(select(PurchaseOrder).options(selectinload(PurchaseOrder.vendor), selectinload(PurchaseOrder.sku))):
        suppliers[po.sku.category_id][po.vendor.id] = po.vendor
    return suppliers


# --- Inventory -------------------------------------------------------------------------------
@dataclass
class SkuPosition:
    sku: Sku
    on_hand: int
    stock_value: float
    aged_units: int
    aged_value: float
    units_sold_12w: int
    avg_weekly_units: float
    weeks_of_cover: float | None  # None when nothing sold recently
    stockout_risk: bool


@dataclass
class CategoryPosition:
    name: str
    stock_value: float
    sell_through: float  # last 12 weeks: sold / (sold + on hand)
    weeks_of_cover: float | None
    aged_share: float
    status: str
    suggested_action: str


@dataclass
class InventorySnapshot:
    as_of: date
    total_value: float
    sku_count: int
    turns: float  # trailing 52-week COGS / current stock value
    aged_value: float
    stockout_risk_skus: int
    skus: list[SkuPosition]
    categories: list[CategoryPosition]


def _category_status(weeks_of_cover: float | None, aged_share: float, stockout_count: int) -> tuple[str, str]:
    # Rule-based for now; the forecast model replaces these suggestions later.
    if aged_share >= 0.25:
        return "Aging", "Review aging SKUs for markdown or write-off"
    if weeks_of_cover is None or weeks_of_cover > 16:
        return "Overstocked", "Reduce or delay the next PO"
    if stockout_count:
        return "Stockout risk", f"Reorder {stockout_count} SKU{'s' if stockout_count > 1 else ''} now"
    return "Healthy", "No action needed"


def inventory_snapshot(db: Session, as_of: date | None = None) -> InventorySnapshot:
    as_of = as_of or settings.as_of
    aging_cutoff = as_of - timedelta(days=settings.aging_days)
    sold_12w = dict(
        db.execute(
            select(SalesWeek.sku_id, func.sum(SalesWeek.units))
            .where(SalesWeek.week_start > as_of - timedelta(weeks=12), SalesWeek.week_start <= as_of)
            .group_by(SalesWeek.sku_id)
        ).all()
    )
    cogs_52w = db.scalar(
        select(func.coalesce(func.sum(SalesWeek.cogs), 0.0)).where(
            SalesWeek.week_start > as_of - timedelta(weeks=52), SalesWeek.week_start <= as_of
        )
    )

    positions: list[SkuPosition] = []
    for sku in db.scalars(
        select(Sku).options(selectinload(Sku.lots), selectinload(Sku.category), selectinload(Sku.vendor)).order_by(Sku.code)
    ):
        on_hand = sku.on_hand
        aged_units = sum(lot.quantity for lot in sku.lots if lot.received_date <= aging_cutoff)
        sold = int(sold_12w.get(sku.id, 0))
        avg = sold / 12
        cover = on_hand / avg if avg else None
        positions.append(
            SkuPosition(
                sku=sku,
                on_hand=on_hand,
                stock_value=sum(lot.quantity * lot.unit_cost for lot in sku.lots),
                aged_units=aged_units,
                aged_value=sum(lot.quantity * lot.unit_cost for lot in sku.lots if lot.received_date <= aging_cutoff),
                units_sold_12w=sold,
                avg_weekly_units=avg,
                weeks_of_cover=cover,
                stockout_risk=cover is not None and cover * 7 < 30,
            )
        )

    by_category: dict[str, list[SkuPosition]] = defaultdict(list)
    for p in positions:
        by_category[p.sku.category.name].append(p)
    categories = []
    for name, items in by_category.items():
        value = sum(p.stock_value for p in items)
        on_hand = sum(p.on_hand for p in items)
        sold = sum(p.units_sold_12w for p in items)
        avg = sold / 12
        cover = on_hand / avg if avg else None
        aged_share = sum(p.aged_value for p in items) / value if value else 0.0
        status, action = _category_status(cover, aged_share, sum(p.stockout_risk for p in items))
        categories.append(
            CategoryPosition(
                name=name,
                stock_value=value,
                sell_through=sold / (sold + on_hand) if sold + on_hand else 0.0,
                weeks_of_cover=cover,
                aged_share=aged_share,
                status=status,
                suggested_action=action,
            )
        )
    categories.sort(key=lambda c: c.stock_value, reverse=True)

    total_value = sum(p.stock_value for p in positions)
    return InventorySnapshot(
        as_of=as_of,
        total_value=total_value,
        sku_count=len(positions),
        turns=cogs_52w / total_value if total_value else 0.0,
        aged_value=sum(p.aged_value for p in positions),
        stockout_risk_skus=sum(p.stockout_risk for p in positions),
        skus=positions,
        categories=categories,
    )

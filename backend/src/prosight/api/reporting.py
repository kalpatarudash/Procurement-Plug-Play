"""Read endpoints: financials, inventory, vendors, purchase orders, ledger."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import analytics, emissions
from ..config import settings
from ..db import get_db
from ..models import JournalEntry, PurchaseOrder, Sku, Vendor

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


# --- Financials ------------------------------------------------------------------------------
class PeriodFigures(BaseModel):
    start: date
    end: date
    revenue: float
    cogs: float
    gross_profit: float
    gross_margin: float
    operating_expenses: float
    taxes_interest: float
    net_income: float
    net_margin: float


class MonthPoint(BaseModel):
    month: date
    revenue: float


class FinancialsOut(BaseModel):
    currency: str
    label: str
    current: PeriodFigures
    prior_year: PeriodFigures
    revenue_growth: float | None
    net_income_growth: float | None
    monthly_revenue: list[MonthPoint]


def _figures(s) -> PeriodFigures:
    return PeriodFigures(
        start=s.start,
        end=s.end,
        revenue=round(s.revenue, 2),
        cogs=round(s.cogs, 2),
        gross_profit=round(s.gross_profit, 2),
        gross_margin=s.gross_profit / s.revenue if s.revenue else 0.0,
        operating_expenses=round(s.operating_expenses, 2),
        taxes_interest=round(s.taxes_interest, 2),
        net_income=round(s.net_income, 2),
        net_margin=s.net_income / s.revenue if s.revenue else 0.0,
    )


def _growth(current: float, prior: float) -> float | None:
    return current / prior - 1 if prior else None


@router.get("/financials", response_model=FinancialsOut, tags=["financials"])
def get_financials(
    db: Db,
    view: Literal["YTD", "QTD", "MTD"] = "YTD",
    start: date | None = Query(None, description="Custom range start; overrides `view` when given with `end`."),
    end: date | None = None,
):
    if start and end:
        if start > end:
            raise HTTPException(422, "start must be on or before end")
        label = "Custom range"
    else:
        start, end = analytics.period_for_view(view, settings.as_of)
        label = view
    current = analytics.financial_summary(db, start, end)
    prior = analytics.financial_summary(db, analytics.shift_year(start, -1), analytics.shift_year(end, -1))
    return FinancialsOut(
        currency=settings.currency,
        label=label,
        current=_figures(current),
        prior_year=_figures(prior),
        revenue_growth=_growth(current.revenue, prior.revenue),
        net_income_growth=_growth(current.net_income, prior.net_income),
        monthly_revenue=[MonthPoint(month=m, revenue=round(r, 2)) for m, r in analytics.monthly_revenue(db, start, end)],
    )


# --- Inventory -------------------------------------------------------------------------------
class CategoryOut(BaseModel):
    name: str
    stock_value: float
    sell_through: float
    weeks_of_cover: float | None
    aged_share: float
    status: str
    suggested_action: str


class SkuOut(BaseModel):
    id: int
    code: str
    name: str
    category: str
    vendor: str  # preferred vendor
    vendor_id: int
    alternative_vendors: list[str]  # other vendors already supplying this category
    unit_cost: float
    unit_price: float
    on_hand: int
    stock_value: float
    aged_units: int
    avg_weekly_units: float
    weeks_of_cover: float | None
    stockout_risk: bool
    status: str  # Stockout risk | Aging | Overstocked | Healthy


class InventoryOut(BaseModel):
    as_of: date
    currency: str
    total_value: float
    sku_count: int
    turns: float
    aged_value: float
    aged_share: float
    stockout_risk_skus: int
    categories: list[CategoryOut]


@router.get("/inventory", response_model=InventoryOut, tags=["inventory"])
def get_inventory(db: Db):
    snap = analytics.inventory_snapshot(db)
    return InventoryOut(
        as_of=snap.as_of,
        currency=settings.currency,
        total_value=round(snap.total_value, 2),
        sku_count=snap.sku_count,
        turns=round(snap.turns, 2),
        aged_value=round(snap.aged_value, 2),
        aged_share=snap.aged_value / snap.total_value if snap.total_value else 0.0,
        stockout_risk_skus=snap.stockout_risk_skus,
        categories=[CategoryOut(**{**c.__dict__, "stock_value": round(c.stock_value, 2)}) for c in snap.categories],
    )


def _sku_status(p: analytics.SkuPosition) -> str:
    if p.stockout_risk:
        return "Stockout risk"
    if p.aged_units:
        return "Aging"
    if p.weeks_of_cover is None or p.weeks_of_cover > 16:
        return "Overstocked"
    return "Healthy"


@router.get("/inventory/skus", response_model=list[SkuOut], tags=["inventory"])
def list_skus(db: Db):
    suppliers = analytics.category_suppliers(db)
    return [
        SkuOut(
            id=p.sku.id,
            code=p.sku.code,
            name=p.sku.name,
            category=p.sku.category.name,
            vendor=p.sku.vendor.name,
            vendor_id=p.sku.vendor.id,
            alternative_vendors=sorted(v.name for v in suppliers[p.sku.category_id].values() if v.id != p.sku.vendor.id),
            unit_cost=p.sku.unit_cost,
            unit_price=p.sku.unit_price,
            on_hand=p.on_hand,
            stock_value=round(p.stock_value, 2),
            aged_units=p.aged_units,
            avg_weekly_units=round(p.avg_weekly_units, 2),
            weeks_of_cover=round(p.weeks_of_cover, 1) if p.weeks_of_cover is not None else None,
            stockout_risk=p.stockout_risk,
            status=_sku_status(p),
        )
        for p in analytics.inventory_snapshot(db).skus
    ]


# --- Vendors & purchase orders ----------------------------------------------------------------
class VendorOut(BaseModel):
    id: int
    name: str
    on_time_rate: float
    lead_time_days: int
    price_index: float
    naics_code: str
    industry: str
    industry_kg_co2e_per_usd: float
    emission_intensity_index: float
    kg_co2e_per_usd: float
    preferred_sku_count: int  # items that list this vendor as preferred
    categories: list[str]  # categories this vendor supplies
    open_po_count: int
    open_po_value: float
    spend_12m: float  # PO value issued in the last 12 months
    last_po_date: date | None


@router.get("/vendors", response_model=list[VendorOut], tags=["vendors"])
def list_vendors(db: Db):
    preferred: dict[int, int] = defaultdict(int)
    category_name = {}
    for sku in db.scalars(select(Sku).options(selectinload(Sku.category))):
        preferred[sku.vendor_id] += 1
        category_name[sku.category_id] = sku.category.name
    supplies: dict[int, set[str]] = defaultdict(set)
    for category_id, vendors in analytics.category_suppliers(db).items():
        for vendor_id in vendors:
            supplies[vendor_id].add(category_name.get(category_id, "?"))
    open_count: dict[int, int] = defaultdict(int)
    open_value: dict[int, float] = defaultdict(float)
    spend: dict[int, float] = defaultdict(float)
    last_po: dict[int, date] = {}
    year_ago = settings.as_of - timedelta(days=365)
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status != "cancelled")):
        if po.status == "open":
            open_count[po.vendor_id] += 1
            open_value[po.vendor_id] += po.amount
        if po.issued_date > year_ago:
            spend[po.vendor_id] += po.amount
        last_po[po.vendor_id] = max(last_po.get(po.vendor_id, po.issued_date), po.issued_date)

    out = []
    for v in db.scalars(select(Vendor).order_by(Vendor.name)):
        industry = emissions.industry_factor(v.naics_code)
        out.append(VendorOut(
            id=v.id, name=v.name, on_time_rate=v.on_time_rate, lead_time_days=v.lead_time_days,
            price_index=v.price_index, naics_code=v.naics_code, industry=industry.title,
            industry_kg_co2e_per_usd=industry.kg_co2e_per_usd, emission_intensity_index=v.emission_intensity_index,
            kg_co2e_per_usd=round(emissions.vendor_factor(v), 4),
            preferred_sku_count=preferred[v.id], categories=sorted(supplies[v.id]),
            open_po_count=open_count[v.id], open_po_value=round(open_value[v.id], 2),
            spend_12m=round(spend[v.id], 2), last_po_date=last_po.get(v.id),
        ))
    return out


class PurchaseOrderOut(BaseModel):
    id: int
    number: str
    vendor: str
    sku_code: str
    sku_name: str
    quantity: int
    unit_cost: float
    amount: float
    currency: str
    status: str
    issued_date: date
    expected_date: date
    received_date: date | None
    external_system: str | None
    external_ref: str | None


def po_out(po: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=po.id,
        number=po.number,
        vendor=po.vendor.name,
        sku_code=po.sku.code,
        sku_name=po.sku.name,
        quantity=po.quantity,
        unit_cost=po.unit_cost,
        amount=po.amount,
        currency=po.currency,
        status=po.status,
        issued_date=po.issued_date,
        expected_date=po.expected_date,
        received_date=po.received_date,
        external_system=po.external_system,
        external_ref=po.external_ref,
    )


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut], tags=["purchase orders"])
def list_purchase_orders(db: Db, status: Literal["open", "received", "cancelled"] | None = None, limit: int = Query(100, le=1000)):
    query = select(PurchaseOrder).options(selectinload(PurchaseOrder.vendor), selectinload(PurchaseOrder.sku))
    if status:
        query = query.where(PurchaseOrder.status == status)
    return [po_out(po) for po in db.scalars(query.order_by(PurchaseOrder.issued_date.desc()).limit(limit))]


# --- Ledger (ABOR) ---------------------------------------------------------------------------
class JournalLineOut(BaseModel):
    account_code: str
    account_name: str
    debit: float
    credit: float


class JournalEntryOut(BaseModel):
    id: int
    number: str
    entry_date: date
    memo: str
    ledger: str
    source_type: str
    source_id: int
    currency: str
    posted_to: str
    external_ref: str | None
    lines: list[JournalLineOut]


def entry_out(e: JournalEntry) -> JournalEntryOut:
    return JournalEntryOut(
        id=e.id,
        number=e.number,
        entry_date=e.entry_date,
        memo=e.memo,
        ledger=e.ledger,
        source_type=e.source_type,
        source_id=e.source_id,
        currency=e.currency,
        posted_to=e.posted_to,
        external_ref=e.external_ref,
        lines=[JournalLineOut(account_code=l.account_code, account_name=l.account_name,
                              debit=l.debit_cents / 100, credit=l.credit_cents / 100) for l in e.lines],
    )


@router.get("/ledger/entries", response_model=list[JournalEntryOut], tags=["ledger"])
def list_journal_entries(db: Db):
    entries = db.scalars(select(JournalEntry).options(selectinload(JournalEntry.lines)).order_by(JournalEntry.id.desc()))
    return [entry_out(e) for e in entries]

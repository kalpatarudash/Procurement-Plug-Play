"""Fiscal-year planning.

Revenue target → required growth, COGS at the current gross margin, and the purchase budget
    purchases = target COGS + target ending inventory − current inventory,
    with ending inventory = target COGS / current inventory turns.

Emission target (% below the trailing-12-month baseline) → projected emissions at the planned
spend, the gap to close, and reduction levers ranked by cost per tonne. The cheapest levers that
close the gap are marked as selected.

Baseline = trailing 52 weeks to the as-of date, so it is a full year even mid-fiscal-year.
"""

from collections import defaultdict
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import analytics, emissions
from .config import settings
from .models import PurchaseOrder, Sku

# Levers that are not derived from your own data use these stated assumptions.
ASSUMPTIONS = {
    "renewable_premium_usd_per_kwh": 0.015,  # green tariff / REC premium over grid power
    "fleet_electrification_share": 0.5,  # share of diesel fleet replaced with EVs
    "ev_kwh_per_diesel_gallon": 7.5,  # EV energy for the miles one diesel gallon covers
    "fleet_cost_usd_per_diesel_gallon": 1.20,  # annualized EV premium net of fuel savings
}


class RevenuePlan(BaseModel):
    baseline: float
    prior_baseline: float
    trend_growth: float
    projected_at_trend: float
    target: float
    required_growth: float
    gap_to_trend: float  # positive = target is above the current trajectory


class MarginPlan(BaseModel):
    gross_margin: float
    target_gross_profit: float
    target_cogs: float


class ProcurementPlan(BaseModel):
    current_inventory: float
    inventory_turns: float
    target_ending_inventory: float
    purchase_budget: float
    baseline_purchases: float
    change_vs_baseline: float | None


class MonthPlan(BaseModel):
    month: date
    revenue_target: float
    purchase_budget: float


class ScopeFigures(BaseModel):
    scope1: float
    scope2: float
    scope3: float
    total: float


class Lever(BaseModel):
    id: str
    scope: int
    title: str
    detail: str
    reduction_t: float
    annual_cost: float  # negative = saves money
    cost_per_t: float | None
    basis: str  # "data" (from your POs and EPA factors) or "assumption"
    selected: bool = False


class EmissionsPlan(BaseModel):
    reduction_pct: float
    baseline: ScopeFigures
    projected: ScopeFigures
    target_total: float
    gap: float
    levers: list[Lever]
    selected_reduction: float
    investment: float
    on_track: bool
    # 0..1: how far the plan is from the emission target; step 3 weights vendor choice by this.
    emission_pressure: float
    sources: dict[str, str]


class PlanAnalysis(BaseModel):
    fiscal_year: int
    start: date
    end: date
    baseline_start: date
    baseline_end: date
    currency: str
    revenue: RevenuePlan
    margins: MarginPlan
    procurement: ProcurementPlan
    monthly: list[MonthPlan]
    emissions: EmissionsPlan
    assumptions: dict[str, float]


def plan_fiscal_year(as_of: date) -> tuple[int, date, date]:
    """The fiscal year after the one containing `as_of`, labelled by the year it ends in."""
    start = analytics.add_months(analytics.fiscal_year_start(as_of), 12)
    end = analytics.add_months(start, 12) - timedelta(days=1)
    return end.year, start, end


def baseline_window(as_of: date) -> tuple[date, date]:
    return as_of - timedelta(days=364), as_of


def default_targets(db: Session) -> tuple[float, float]:
    start, end = baseline_window(settings.as_of)
    revenue = analytics.financial_summary(db, start, end).revenue
    return round(revenue * 1.10, -4), 15.0


def _scope(scope1: float, scope2: float, scope3: float) -> ScopeFigures:
    return ScopeFigures(scope1=round(scope1, 1), scope2=round(scope2, 1), scope3=round(scope3, 1),
                        total=round(scope1 + scope2 + scope3, 1))


def _supplier_levers(db: Session, start: date, end: date, spend_scale: float) -> list[Lever]:
    """Per category: move spend from higher- to the lowest-emitting vendor that already supplies it."""
    suppliers: dict[str, dict[int, object]] = defaultdict(dict)  # category -> vendor id -> vendor
    for sku in db.scalars(select(Sku).options(selectinload(Sku.category), selectinload(Sku.vendor))):
        suppliers[sku.category.name][sku.vendor.id] = sku.vendor
    spend: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for po in db.scalars(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.vendor), selectinload(PurchaseOrder.sku).selectinload(Sku.category))
        .where(PurchaseOrder.issued_date.between(start, end), PurchaseOrder.status != "cancelled")
    ):
        suppliers[po.sku.category.name][po.vendor.id] = po.vendor
        spend[po.sku.category.name][po.vendor.id] += po.amount

    levers = []
    for category, vendors in sorted(suppliers.items()):
        best = min(vendors.values(), key=emissions.vendor_factor)
        best_factor = emissions.vendor_factor(best)
        reduction = cost = moved = 0.0
        for vendor_id, amount in spend[category].items():
            vendor = vendors[vendor_id]
            if vendor_id == best.id:
                continue
            new_amount = amount * best.price_index / vendor.price_index
            reduction += (amount * emissions.vendor_factor(vendor) - new_amount * best_factor) / 1000
            cost += new_amount - amount
            moved += amount
        reduction, cost, moved = reduction * spend_scale, cost * spend_scale, moved * spend_scale
        if reduction >= 1.0:  # ignore shifts worth less than a tonne
            levers.append(Lever(
                id=f"supplier:{category}",
                scope=3,
                title=f"Shift {category} orders to {best.name}",
                detail=f"Move ${moved:,.0f} of planned spend to the lowest-emission vendor already supplying this category.",
                reduction_t=round(reduction, 1),
                annual_cost=round(cost, 2),
                cost_per_t=None,
                basis="data",
            ))
    return levers


def build_plan(db: Session, revenue_target: float, reduction_pct: float) -> PlanAnalysis:
    as_of = settings.as_of
    fiscal_year, start, end = plan_fiscal_year(as_of)
    b_start, b_end = baseline_window(as_of)
    base = analytics.financial_summary(db, b_start, b_end)
    prior = analytics.financial_summary(db, b_start - timedelta(days=364), b_start - timedelta(days=1))

    # --- Revenue and margin ---
    trend = base.revenue / prior.revenue - 1 if prior.revenue else 0.0
    projected = base.revenue * (1 + trend)
    gross_margin = base.gross_profit / base.revenue if base.revenue else 0.0
    target_cogs = revenue_target * (1 - gross_margin)

    # --- Procurement budget ---
    inventory = analytics.inventory_snapshot(db, as_of)
    turns = inventory.turns or 1.0
    target_inventory = target_cogs / turns
    purchase_budget = max(0.0, target_cogs + target_inventory - inventory.total_value)
    base_emissions = emissions.emissions_summary(db, b_start, b_end)
    baseline_purchases = base_emissions.purchases_usd

    # Phase the year by last year's revenue seasonality (by calendar month).
    by_calendar_month: dict[int, float] = defaultdict(float)
    for month, revenue in analytics.monthly_revenue(db, b_start, b_end):
        by_calendar_month[month.month] += revenue
    season_total = sum(by_calendar_month.values()) or 1.0
    monthly = []
    for i in range(12):
        month = analytics.add_months(start, i)
        share = by_calendar_month.get(month.month, season_total / 12) / season_total
        monthly.append(MonthPlan(month=month, revenue_target=round(revenue_target * share, 2),
                                 purchase_budget=round(purchase_budget * share, 2)))

    # --- Emissions ---
    spend_scale = purchase_budget / baseline_purchases if baseline_purchases else 1.0
    proj_scope3 = base_emissions.scope3 * spend_scale
    projected_total = base_emissions.scope1 + base_emissions.scope2 + proj_scope3
    target_total = base_emissions.total * (1 - reduction_pct / 100)
    gap = max(0.0, projected_total - target_total)

    levers = _supplier_levers(db, b_start, b_end, spend_scale)
    if base_emissions.electricity_kwh:
        levers.append(Lever(
            id="renewable_power",
            scope=2,
            title="Move sites to renewable power",
            detail=f"Green tariff for {base_emissions.electricity_kwh:,.0f} kWh a year (market-based Scope 2).",
            reduction_t=round(base_emissions.scope2, 1),
            annual_cost=round(base_emissions.electricity_kwh * ASSUMPTIONS["renewable_premium_usd_per_kwh"], 2),
            cost_per_t=None,
            basis="assumption",
        ))
    if base_emissions.diesel_gallons:
        gallons = base_emissions.diesel_gallons * ASSUMPTIONS["fleet_electrification_share"]
        added_kwh = gallons * ASSUMPTIONS["ev_kwh_per_diesel_gallon"]
        levers.append(Lever(
            id="fleet_electrification",
            scope=1,
            title="Electrify warehouse fleet",
            detail=f"Replace {ASSUMPTIONS['fleet_electrification_share']:.0%} of diesel use ({gallons:,.0f} gal) with EVs on grid power.",
            reduction_t=round((gallons * emissions.DIESEL_KG_PER_GALLON - added_kwh * emissions.ELECTRICITY_KG_PER_KWH) / 1000, 1),
            annual_cost=round(gallons * ASSUMPTIONS["fleet_cost_usd_per_diesel_gallon"], 2),
            cost_per_t=None,
            basis="assumption",
        ))
    for lever in levers:
        lever.cost_per_t = round(lever.annual_cost / lever.reduction_t, 2) if lever.reduction_t else None
    levers.sort(key=lambda l: l.cost_per_t if l.cost_per_t is not None else float("inf"))

    covered = investment = 0.0
    for lever in levers:
        if covered >= gap:
            break
        lever.selected = True
        covered += lever.reduction_t
        investment += max(0.0, lever.annual_cost)

    return PlanAnalysis(
        fiscal_year=fiscal_year,
        start=start,
        end=end,
        baseline_start=b_start,
        baseline_end=b_end,
        currency=settings.currency,
        revenue=RevenuePlan(
            baseline=round(base.revenue, 2),
            prior_baseline=round(prior.revenue, 2),
            trend_growth=trend,
            projected_at_trend=round(projected, 2),
            target=revenue_target,
            required_growth=revenue_target / base.revenue - 1 if base.revenue else 0.0,
            gap_to_trend=round(revenue_target - projected, 2),
        ),
        margins=MarginPlan(
            gross_margin=gross_margin,
            target_gross_profit=round(revenue_target * gross_margin, 2),
            target_cogs=round(target_cogs, 2),
        ),
        procurement=ProcurementPlan(
            current_inventory=round(inventory.total_value, 2),
            inventory_turns=round(turns, 2),
            target_ending_inventory=round(target_inventory, 2),
            purchase_budget=round(purchase_budget, 2),
            baseline_purchases=round(baseline_purchases, 2),
            change_vs_baseline=purchase_budget / baseline_purchases - 1 if baseline_purchases else None,
        ),
        monthly=monthly,
        emissions=EmissionsPlan(
            reduction_pct=reduction_pct,
            baseline=_scope(base_emissions.scope1, base_emissions.scope2, base_emissions.scope3),
            projected=_scope(base_emissions.scope1, base_emissions.scope2, proj_scope3),
            target_total=round(target_total, 1),
            gap=round(gap, 1),
            levers=levers,
            selected_reduction=round(covered, 1),
            investment=round(investment, 2),
            on_track=covered >= gap,
            emission_pressure=min(1.0, gap / projected_total) if projected_total else 0.0,
            sources=emissions.SOURCES,
        ),
        assumptions=ASSUMPTIONS,
    )

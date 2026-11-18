"""PO recommendations: when to order each SKU, how much, and from which vendor.

Per SKU, using the LightGBM forecast:
  - Project stock week by week: on hand + open POs arriving − forecast demand.
  - Safety stock = z × forecast error σ × √(lead time + review period), z for a 95% service level.
  - Order-by date: the last weekly review at which an order still arrives (after the vendor's lead
    time) before projected stock drops below safety stock. "Order now" when that is this week.
  - Quantity (order-up-to): forecast demand through lead time + review + one order cycle, plus
    safety stock, minus stock on hand and on order by then.

Vendor choice: every vendor already supplying the category is scored on unit cost, reliability
(on-time rate), lead time and emissions per unit (EPA factor × intensity × unit cost); lower is
better. The emissions weight rises with the FY plan's emission pressure (step 2), so an
off-track emissions target steers orders to lower-emitting vendors. A vendor whose lead time
would land after a projected stockout is penalised.
"""

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from . import emissions, forecasting, planning
from .config import settings
from .decisions import skus_on_hold
from .models import Decision, PurchaseOrder, Sku, Vendor

SERVICE_LEVEL_Z = 1.65  # ≈95% cycle service level
REVIEW_WEEKS = 1  # recommendations are reviewed weekly
CYCLE_WEEKS = 6  # each order covers about six weeks of demand after it lands
UPCOMING_WEEKS = 4  # "upcoming" = must be ordered within this many weeks
BASE_WEIGHTS = {"cost": 0.45, "reliability": 0.25, "lead_time": 0.15, "emissions": 0.15}
LATE_PENALTY = 0.5


@dataclass
class VendorOption:
    vendor_id: int
    name: str
    unit_cost: float
    lead_weeks: int
    on_time_rate: float
    kg_co2e_per_unit: float
    arrives_before_stockout: bool
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)


@dataclass
class SkuPlan:
    sku_id: int
    sku_code: str
    sku_name: str
    category: str
    status: str  # order_now | upcoming | healthy | overstocked
    on_hand: int
    on_order: int
    weekly_forecast: list[float]
    forecast_units: float  # demand over lead time + review + cycle (chosen vendor)
    safety_stock: float
    weeks_of_cover: float | None
    stockout_date: date | None
    order_by: date | None
    quantity: int
    amount: float
    kg_co2e: float
    impact_usd: float  # gross margin protected from stockouts
    confidence: str
    backtest_wape: float | None
    vendor: VendorOption | None
    alternatives: list[VendorOption]
    reason: str


@dataclass
class RecommendationRun:
    as_of: date
    weights: dict[str, float]
    emission_pressure: float
    forecast: dict
    plans: list[SkuPlan]
    budget: list[dict]
    plan_alignment: dict


def scoring_weights(emission_pressure: float) -> dict[str, float]:
    em = BASE_WEIGHTS["emissions"] + 0.35 * emission_pressure
    rest = sum(v for k, v in BASE_WEIGHTS.items() if k != "emissions")
    return {k: (em if k == "emissions" else v * (1 - em) / rest) for k, v in BASE_WEIGHTS.items()}


def _confidence(wape: float | None) -> str:
    if wape is None:
        return "Low"
    return "High" if wape < 0.20 else "Medium" if wape < 0.35 else "Low"


def _projection(on_hand: int, arrivals: dict[int, int], demand: list[float]) -> list[float]:
    """Stock at the end of each forecast week (index 0 = now)."""
    stock = [float(on_hand)]
    for k, d in enumerate(demand, start=1):
        stock.append(stock[-1] + arrivals.get(k, 0) - d)
    return stock


def _score(options: list[VendorOption], weights: dict[str, float]) -> None:
    metrics = {
        "cost": [o.unit_cost for o in options],
        "reliability": [1 - o.on_time_rate for o in options],
        "lead_time": [o.lead_weeks for o in options],
        "emissions": [o.kg_co2e_per_unit for o in options],
    }
    for idx, option in enumerate(options):
        comps = {}
        for name, values in metrics.items():
            lo, hi = min(values), max(values)
            comps[name] = round(weights[name] * ((values[idx] - lo) / (hi - lo) if hi > lo else 0.0), 4)
        if not option.arrives_before_stockout:
            comps["late_delivery"] = LATE_PENALTY
        option.components = comps
        option.score = round(sum(comps.values()), 4)
    options.sort(key=lambda o: o.score)


def build_recommendations(db: Session) -> RecommendationRun:
    as_of = settings.as_of
    fc = forecasting.get_forecast(db)
    plan = planning.build_plan(db, *_current_targets(db))
    pressure = plan.emissions.emission_pressure
    weights = scoring_weights(pressure)

    skus = db.scalars(
        select(Sku).options(selectinload(Sku.lots), selectinload(Sku.category), selectinload(Sku.vendor)).order_by(Sku.code)
    ).all()
    open_pos = db.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "open")).all()
    arrivals: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    on_order: dict[int, int] = defaultdict(int)
    for po in open_pos:
        week = max(1, math.ceil((po.expected_date - as_of).days / 7))  # overdue POs assumed next week
        arrivals[po.sku_id][week] += po.quantity
        on_order[po.sku_id] += po.quantity

    # Vendors that already supply each category (from item master and PO history).
    suppliers: dict[int, dict[int, Vendor]] = defaultdict(dict)
    for sku in skus:
        suppliers[sku.category_id][sku.vendor.id] = sku.vendor
    for po in db.scalars(select(PurchaseOrder).options(selectinload(PurchaseOrder.vendor), selectinload(PurchaseOrder.sku))):
        suppliers[po.sku.category_id][po.vendor.id] = po.vendor

    plans = []
    for sku in skus:
        f = fc.skus[sku.id]
        demand = f.weekly
        stock = _projection(sku.on_hand, arrivals[sku.id], demand)
        stockout_week = next((k for k, s in enumerate(stock) if s < 0), None)
        avg = sum(demand[:8]) / 8

        options = []
        for vendor in suppliers[sku.category_id].values():
            lead = math.ceil(vendor.lead_time_days / 7)
            unit_cost = round(sku.unit_cost * vendor.price_index, 2)
            options.append(VendorOption(
                vendor_id=vendor.id, name=vendor.name, unit_cost=unit_cost, lead_weeks=lead,
                on_time_rate=vendor.on_time_rate,
                kg_co2e_per_unit=round(unit_cost * emissions.vendor_factor(vendor), 3),
                arrives_before_stockout=stockout_week is None or lead <= stockout_week,
            ))
        _score(options, weights)
        best = options[0]

        L = best.lead_weeks
        reach = min(L + REVIEW_WEEKS + CYCLE_WEEKS, forecasting.HORIZON)
        safety = SERVICE_LEVEL_Z * f.sigma * math.sqrt(L + REVIEW_WEEKS)
        below_safety = next((k for k, s in enumerate(stock) if s < safety), None)
        order_week = None if below_safety is None else below_safety - L - REVIEW_WEEKS
        need = sum(demand[:reach]) + safety - sku.on_hand - sum(q for wk, q in arrivals[sku.id].items() if wk <= reach)
        quantity = max(0, math.ceil(need))

        cover = (sku.on_hand + on_order[sku.id]) / avg if avg else None
        if order_week is not None and order_week <= 0 and quantity > 0:
            status = "order_now"
        elif order_week is not None and 0 < order_week <= UPCOMING_WEEKS:
            status = "upcoming"
        elif cover is not None and cover > 16:
            status = "overstocked"
        else:
            status = "healthy"

        actionable = status in ("order_now", "upcoming") and quantity > 0
        lost_units = max(0.0, -stock[min(reach, len(stock) - 1)])
        impact = lost_units * (sku.unit_price - sku.unit_cost) if status == "order_now" else 0.0
        amount = round(quantity * best.unit_cost, 2)
        stockout_date = as_of + timedelta(weeks=stockout_week) if stockout_week is not None else None
        order_by = as_of + timedelta(weeks=max(0, order_week)) if order_week is not None else None

        if status == "order_now":
            why_vendor = (f"{best.name}: ${best.unit_cost:.2f}/unit, {best.on_time_rate:.0%} on time, {best.lead_weeks}-week lead time, "
                          f"{best.kg_co2e_per_unit:.2f} kg CO₂e/unit")
            if len(options) > 1:
                why_vendor += f" (best of {len(options)} vendors; emissions weight {weights['emissions']:.0%})"
            reason = (f"Forecast demand is {sum(demand[:L + REVIEW_WEEKS]):,.0f} units over the {L + REVIEW_WEEKS} weeks before a new order "
                      f"can land, against {sku.on_hand:,} on hand and {on_order[sku.id]:,} on order"
                      + (f"; stock runs out around {stockout_date:%b %-d}" if stockout_date else "; stock would fall below safety stock")
                      + f". {why_vendor}.")
        elif status == "upcoming":
            reason = (f"Reorder by {order_by:%b %-d} to stay above safety stock"
                      + (f"; ordering today would be {quantity:,} units from {best.name} at ${best.unit_cost:.2f}/unit." if quantity > 0 else "."))
        elif status == "overstocked":
            reason = f"{cover:.0f} weeks of cover including open POs; no new order needed."
        else:
            reason = "Stock and open POs cover forecast demand."

        plans.append(SkuPlan(
            sku_id=sku.id, sku_code=sku.code, sku_name=sku.name, category=sku.category.name, status=status,
            on_hand=sku.on_hand, on_order=on_order[sku.id], weekly_forecast=demand,
            forecast_units=round(sum(demand[:reach]), 1), safety_stock=round(safety, 1),
            weeks_of_cover=round(cover, 1) if cover is not None else None,
            stockout_date=stockout_date, order_by=order_by,
            # Upcoming SKUs carry the quantity they would need if ordered today (admin can raise early).
            quantity=quantity if actionable else 0, amount=amount if actionable else 0.0,
            kg_co2e=round(quantity * best.kg_co2e_per_unit, 1) if actionable else 0.0,
            impact_usd=round(impact, 2), confidence=_confidence(f.backtest_wape), backtest_wape=f.backtest_wape,
            vendor=best, alternatives=options[1:], reason=reason,
        ))

    order_rank = {"order_now": 0, "upcoming": 1, "overstocked": 2, "healthy": 3}
    plans.sort(key=lambda p: (order_rank[p.status], -p.impact_usd, p.order_by or date.max))

    # Recommended spend vs. the FY plan's purchase budget, by the month goods arrive.
    monthly_budget = {m.month: m.purchase_budget for m in plan.monthly}
    by_month: dict[date, float] = defaultdict(float)
    for p in plans:
        if p.status == "order_now" and p.vendor:
            by_month[(as_of + timedelta(weeks=p.vendor.lead_weeks)).replace(day=1)] += p.amount
    budget = [
        {"month": m, "recommended": round(v, 2), "plan_budget": monthly_budget.get(m),
         "within_budget": monthly_budget.get(m) is None or v <= monthly_budget[m]}
        for m, v in sorted(by_month.items())
    ]

    # Does forecast demand support the revenue target? Compare the forecast weeks inside the plan year.
    forecast_rev = plan_rev = 0.0
    monthly_target = {m.month: m.revenue_target for m in plan.monthly}
    price = {s.id: s.unit_price for s in skus}
    for h, week in enumerate(fc.forecast_weeks):
        month = week.replace(day=1)
        if month not in monthly_target:
            continue
        forecast_rev += sum(fc.skus[s.id].weekly[h] * price[s.id] for s in skus)
        plan_rev += monthly_target[month] * 7 / 30.4
    alignment = {
        "forecast_revenue": round(forecast_rev, 2),
        "plan_revenue": round(plan_rev, 2),
        "gap_pct": forecast_rev / plan_rev - 1 if plan_rev else None,
        "weeks_compared": sum(1 for w in fc.forecast_weeks if w.replace(day=1) in monthly_target),
    }

    return RecommendationRun(
        as_of=as_of,
        weights={k: round(v, 3) for k, v in weights.items()},
        emission_pressure=round(pressure, 3),
        forecast={
            "model": "LightGBM (Poisson, direct multi-horizon)",
            "trained_at": fc.trained_at,
            "last_week": fc.last_week,
            "horizon_weeks": forecasting.HORIZON,
            "backtest_weeks": forecasting.BACKTEST_WEEKS,
            "backtest_wape": round(fc.backtest_wape, 4),
            "naive_wape": round(fc.naive_wape, 4),
            "backtest_bias": round(fc.backtest_bias, 4),
            "train_rows": fc.train_rows,
            "top_features": list(fc.feature_importance)[:5],
        },
        plans=plans,
        budget=budget,
        plan_alignment=alignment,
    )


def _current_targets(db: Session) -> tuple[float, float]:
    from .models import PlanTarget

    fiscal_year, _, _ = planning.plan_fiscal_year(settings.as_of)
    saved = db.get(PlanTarget, fiscal_year)
    return (saved.revenue_target, saved.emission_reduction_pct) if saved else planning.default_targets(db)


def _decision_from_plan(p: SkuPlan, run: RecommendationRun, raised_by: str) -> Decision:
    return Decision(
        kind="purchase_order",
        status="pending",
        source="model",
        title=f"Order {p.quantity:,} × {p.sku_name} from {p.vendor.name}",
        reason=p.reason,
        impact_usd=p.impact_usd,
        payload={"sku_id": p.sku_id, "vendor_id": p.vendor.vendor_id, "quantity": p.quantity, "unit_cost": p.vendor.unit_cost},
        details={
            "sku_code": p.sku_code,
            "category": p.category,
            "confidence": p.confidence,
            "backtest_wape": p.backtest_wape,
            "order_by": p.order_by.isoformat() if p.order_by else None,
            "stockout_date": p.stockout_date.isoformat() if p.stockout_date else None,
            "on_hand": p.on_hand,
            "on_order": p.on_order,
            "forecast_units": p.forecast_units,
            "safety_stock": p.safety_stock,
            "amount": p.amount,
            "kg_co2e": p.kg_co2e,
            "vendor_options": [asdict(o) for o in [p.vendor, *p.alternatives]],
            "weights": run.weights,
            "status_when_raised": p.status,
            "raised_by": raised_by,  # "refresh" (replaced on next refresh) or "admin"
        },
        created_at=datetime.now(UTC),
    )


def refresh_decisions(db: Session) -> tuple[RecommendationRun, list[Decision]]:
    """Replace pending model-generated PO decisions with this run's 'order now' recommendations.

    Approved/rejected decisions and manual ones are never touched.
    """
    run = build_recommendations(db)
    db.execute(delete(Decision).where(
        Decision.source == "model", Decision.kind == "purchase_order", Decision.status == "pending",
        Decision.details["raised_by"].as_string() == "refresh",
    ))
    on_hold = skus_on_hold(db, ("purchase_order",))
    created = [_decision_from_plan(p, run, "refresh") for p in run.plans
               if p.status == "order_now" and p.vendor and p.quantity > 0 and p.sku_id not in on_hold]
    db.add_all(created)
    db.commit()
    return run, created


class NothingToOrder(Exception):
    pass


def raise_early(db: Session, sku_id: int) -> Decision:
    """Turn one SKU's recommendation (order now or upcoming) into a pending decision today."""
    run = build_recommendations(db)
    p = next((p for p in run.plans if p.sku_id == sku_id), None)
    if p is None or not p.vendor or p.quantity <= 0:
        raise NothingToOrder("Stock and open POs already cover this SKU's forecast demand.")
    existing = db.scalar(select(Decision).where(
        Decision.kind == "purchase_order", Decision.status == "pending", Decision.source == "model"
    ).where(Decision.payload["sku_id"].as_integer() == sku_id))
    if existing:
        return existing
    decision = _decision_from_plan(p, run, "admin")
    db.add(decision)
    db.commit()
    return decision

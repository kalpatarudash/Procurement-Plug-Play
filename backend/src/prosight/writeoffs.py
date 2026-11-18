"""Aging-stock decisions: hold, mark down, or write off.

For each SKU holding stock older than `settings.aging_days`, compare two scenarios over a
sell window, using the LightGBM forecast (extended past its horizon at the recent run rate):

  Hold      Aged units sell first at full price (FIFO) as far as forecast demand allows; the rest
            can never sell, so it is written off now (no point paying to store it).
  Markdown  Aged units go on clearance at a discount; demand rises by the price elasticity. Units
            even clearance cannot move are written off now. Newer stock waits until the aged
            stock clears, so its full-price sales in the window are counted in both scenarios.

Cash = revenue − selling costs − holding cost of the aged units. The scenario with more cash wins
("maintain revenue"). Recommended actions become pending decisions for the admin.

Accounting on approval:
  Write-off  Dr 5150 Inventory write-off expense / Cr 1300 Inventory (oldest lots first).
  Markdown   Lower of cost and net realizable value (ASC 330): if the clearance price net of
             selling costs is below cost, Dr 5160 Inventory write-down / Cr 1300 for the difference.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from . import forecasting
from .config import settings
from .decisions import SELLING_COST_RATE
from .models import Decision, Sku
from .recommendations import _confidence

ASSUMPTIONS = {
    "sell_window_weeks": 26,
    "max_age_days": 365,  # stock older than this is obsolete and cannot be sold
    "holding_cost_rate_annual": 0.25,  # storage, capital, insurance, shrink as a share of cost
    "clearance_discount": 0.40,
    "price_elasticity": 2.0,  # demand lift = 1 + elasticity × discount
    "selling_cost_rate": SELLING_COST_RATE,  # share of the selling price (also used for NRV)
    "min_value_usd": 250,  # ignore actions worth less than this
}
KINDS = ("write_off", "markdown")


@dataclass
class Scenario:
    aged_sold: int
    aged_revenue: float
    newer_revenue: float
    weeks_to_clear: int | None
    holding_cost: float
    write_off_units: int
    write_off_cost: float
    cash: float


@dataclass
class AgingPlan:
    sku_id: int
    sku_code: str
    sku_name: str
    category: str
    on_hand: int
    aged_units: int
    aged_value: float
    oldest_days: int
    unit_price: float
    clearance_price: float
    demand_window: float
    hold: Scenario
    markdown: Scenario
    recommendation: str  # hold | write_off | markdown
    cash_gain_vs_hold: float
    confidence: str
    reason: str
    actions: list[dict]


@dataclass
class WriteOffRun:
    as_of: date
    assumptions: dict
    plans: list[AgingPlan]
    totals: dict


def _window_demand(weekly: list[float], weeks: int) -> list[float]:
    tail = sum(weekly[-4:]) / 4
    return (weekly + [tail] * weeks)[:weeks]


def _sell(lots: list[tuple[int, int]], demand: list[float]) -> tuple[int, int | None, float]:
    """FIFO sale of aged lots, each only before its deadline week (when it hits the max age).

    lots: (quantity, deadline_week), oldest first. Returns units sold, the week the last aged unit
    sells (None if some never sell), and the weekly demand left over for newer stock.
    """
    remaining = list(demand)
    sold, last_week, all_sold = 0.0, 0, True
    for qty, deadline in lots:
        left = float(qty)
        for k in range(min(deadline, len(remaining))):
            if left <= 0:
                break
            take = min(left, remaining[k])
            if take > 0:
                remaining[k] -= take
                left -= take
                last_week = k + 1
        sold += qty - left
        all_sold = all_sold and left < 0.5
    return round(sold), (last_week if all_sold else None), sum(remaining)


def _scenario(lots: list[tuple[int, int]], newer: int, unit_cost: float, aged_price: float, full_price: float,
              demand: list[float], lift: float) -> Scenario:
    a = ASSUMPTIONS
    aged = sum(q for q, _ in lots)
    sold, clear_week, leftover = _sell(lots, [d * lift for d in demand])
    weeks_held = clear_week or len(demand)
    # Leftover lifted demand after clearance reverts to full-price demand for newer stock.
    newer_sold = min(newer, leftover / lift)
    holding = sold * unit_cost * a["holding_cost_rate_annual"] / 52 * weeks_held / 2
    aged_revenue = sold * aged_price
    newer_revenue = newer_sold * full_price
    revenue = aged_revenue + newer_revenue
    return Scenario(
        aged_sold=sold,
        aged_revenue=round(aged_revenue, 2),
        newer_revenue=round(newer_revenue, 2),
        weeks_to_clear=clear_week,
        holding_cost=round(holding, 2),
        write_off_units=aged - sold,
        write_off_cost=round((aged - sold) * unit_cost, 2),
        cash=round(revenue * (1 - a["selling_cost_rate"]) - holding, 2),
    )


def build_writeoffs(db: Session) -> WriteOffRun:
    a = ASSUMPTIONS
    as_of = settings.as_of
    cutoff = as_of - timedelta(days=settings.aging_days)
    fc = forecasting.get_forecast(db)
    lift = 1 + a["price_elasticity"] * a["clearance_discount"]

    plans = []
    for sku in db.scalars(select(Sku).options(selectinload(Sku.lots), selectinload(Sku.category)).order_by(Sku.code)):
        aged_lots = [lot for lot in sku.lots if lot.received_date <= cutoff]
        aged = sum(lot.quantity for lot in aged_lots)
        if aged == 0:
            continue
        aged_value = sum(lot.quantity * lot.unit_cost for lot in aged_lots)
        avg_cost = aged_value / aged
        newer = sku.on_hand - aged
        demand = _window_demand(fc.skus[sku.id].weekly, a["sell_window_weeks"])
        clearance = round(sku.unit_price * (1 - a["clearance_discount"]), 2)

        # Each aged lot must sell before it reaches the max age; past that it is obsolete.
        lots = [(lot.quantity, max(0, (a["max_age_days"] - (as_of - lot.received_date).days) // 7)) for lot in aged_lots]
        hold = _scenario(lots, newer, avg_cost, sku.unit_price, sku.unit_price, demand, 1.0)
        md = _scenario(lots, newer, avg_cost, clearance, sku.unit_price, demand, lift)
        gain = md.cash - hold.cash
        oldest = (as_of - min(lot.received_date for lot in aged_lots)).days

        # Markdown only when it beats holding by a meaningful amount; otherwise hold and write off
        # only what can never sell. Tiny write-offs are not worth raising.
        min_value = a["min_value_usd"]
        actions: list[dict] = []
        if gain >= min_value and md.aged_sold > 0:
            recommendation = "markdown"
            actions.append({"kind": "markdown", "quantity": md.aged_sold, "clearance_price": clearance, "value": round(gain, 2)})
            if md.write_off_units and md.write_off_cost >= min_value:
                actions.append({"kind": "write_off", "quantity": md.write_off_units, "value": md.write_off_cost})
        elif hold.write_off_units and hold.write_off_cost >= min_value:
            recommendation = "write_off"
            actions.append({"kind": "write_off", "quantity": hold.write_off_units, "value": hold.write_off_cost})
        else:
            recommendation = "hold"

        youngest = (as_of - max(lot.received_date for lot in aged_lots)).days
        ages = f"{oldest} days" if youngest == oldest else f"{youngest}–{oldest} days"
        obsolete = all(deadline == 0 for _, deadline in lots)
        intro = (f"{aged:,} units have been in stock {ages}. Before they pass "
                 f"{a['max_age_days']} days, the forecast sells {hold.aged_sold:,} of them at full price")
        if obsolete:
            intro = f"{aged:,} units have been in stock {ages}, past the {a['max_age_days']}-day limit, so they can no longer be sold"
        if recommendation == "markdown":
            reason = (f"{intro}. A {a['clearance_discount']:.0%} markdown to ${clearance:.2f} clears {md.aged_sold:,}"
                      + (f" in about {md.weeks_to_clear} weeks" if md.weeks_to_clear else "")
                      + f" and brings in ${gain:,.0f} more cash than holding"
                      + (f"; the remaining {md.write_off_units:,} won't sell even on clearance." if md.write_off_units else "."))
        elif recommendation == "write_off":
            why = ("Even a markdown can't sell them in time" if md.aged_sold == hold.aged_sold
                   else f"A markdown would bring in only ${gain:,.0f} more" if gain > 0
                   else f"A markdown would bring in ${-gain:,.0f} less cash")
            if obsolete:
                reason = f"{intro}. Write off all {aged:,} (${hold.write_off_cost:,.0f} at cost) and stop paying to store them."
            else:
                reason = (f"{intro}; the other {hold.write_off_units:,} (${hold.write_off_cost:,.0f} at cost) would never sell. "
                          f"{why}, so write off only the excess now and stop paying to store it.")
        elif hold.write_off_units:
            reason = f"{intro}; the {hold.write_off_units:,} left over are worth too little to act on yet."
        else:
            reason = f"{intro}, so it should sell through at full price."

        plans.append(AgingPlan(
            sku_id=sku.id, sku_code=sku.code, sku_name=sku.name, category=sku.category.name, on_hand=sku.on_hand,
            aged_units=aged, aged_value=round(aged_value, 2), oldest_days=oldest, unit_price=sku.unit_price,
            clearance_price=clearance, demand_window=round(sum(demand), 1), hold=hold, markdown=md,
            recommendation=recommendation, cash_gain_vs_hold=round(gain, 2),
            confidence=_confidence(fc.skus[sku.id].backtest_wape), reason=reason, actions=actions,
        ))

    rank = {"markdown": 0, "write_off": 1, "hold": 2}
    plans.sort(key=lambda p: (rank[p.recommendation], -p.aged_value))
    totals = {
        "aged_units": sum(p.aged_units for p in plans),
        "aged_value": round(sum(p.aged_value for p in plans), 2),
        "write_off_cost": round(sum(act["value"] for p in plans for act in p.actions if act["kind"] == "write_off"), 2),
        "markdown_revenue": round(sum(p.markdown.aged_revenue for p in plans if p.recommendation == "markdown"), 2),
        "cash_gain_vs_hold": round(sum(p.cash_gain_vs_hold for p in plans if p.recommendation == "markdown"), 2),
    }
    return WriteOffRun(as_of=as_of, assumptions={**ASSUMPTIONS, "aging_days": settings.aging_days}, plans=plans, totals=totals)


def _decision(p: AgingPlan, action: dict, raised_by: str) -> Decision:
    if action["kind"] == "markdown":
        title = f"Mark down {action['quantity']:,} aged × {p.sku_name} to ${action['clearance_price']:.2f}"
        payload = {"sku_id": p.sku_id, "quantity": action["quantity"], "clearance_price": action["clearance_price"]}
        impact = action["value"]
    else:
        title = f"Write off {action['quantity']:,} aged × {p.sku_name}"
        payload = {"sku_id": p.sku_id, "quantity": action["quantity"]}
        # Value of acting now: storage/capital cost avoided over the sell window.
        impact = round(action["value"] * ASSUMPTIONS["holding_cost_rate_annual"] * ASSUMPTIONS["sell_window_weeks"] / 52, 2)
    return Decision(
        kind=action["kind"],
        status="pending",
        source="model",
        title=title,
        reason=p.reason,
        impact_usd=impact,
        payload=payload,
        details={
            "sku_code": p.sku_code,
            "category": p.category,
            "confidence": p.confidence,
            "on_hand": p.on_hand,
            "aged_units": p.aged_units,
            "aged_value": p.aged_value,
            "oldest_days": p.oldest_days,
            "unit_price": p.unit_price,
            "amount": action["value"] if action["kind"] == "write_off" else round(action["quantity"] * action["clearance_price"], 2),
            "hold": asdict(p.hold),
            "markdown": asdict(p.markdown),
            "assumptions": ASSUMPTIONS,
            "raised_by": raised_by,
        },
        created_at=datetime.now(UTC),
    )


def refresh_decisions(db: Session) -> tuple[WriteOffRun, list[Decision]]:
    """Replace pending model-raised write-off/markdown decisions with this run's actions."""
    run = build_writeoffs(db)
    db.execute(delete(Decision).where(
        Decision.source == "model", Decision.kind.in_(KINDS), Decision.status == "pending",
        Decision.details["raised_by"].as_string() == "refresh",
    ))
    busy = set(db.scalars(select(Decision.payload["sku_id"].as_integer()).where(
        Decision.kind.in_(KINDS), Decision.status == "pending"
    )))
    created = [_decision(p, act, "refresh") for p in run.plans if p.sku_id not in busy for act in p.actions]
    db.add_all(created)
    db.commit()
    return run, created

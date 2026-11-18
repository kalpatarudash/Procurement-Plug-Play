"""Demand forecast and PO recommendations."""

from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import forecasting, recommendations
from ..db import get_db
from ..models import Sku
from .actions import DecisionOut, decision_out

router = APIRouter(tags=["recommendations"])
Db = Annotated[Session, Depends(get_db)]


class VendorOptionOut(BaseModel):
    vendor_id: int
    name: str
    unit_cost: float
    lead_weeks: int
    on_time_rate: float
    kg_co2e_per_unit: float
    arrives_before_stockout: bool
    score: float
    components: dict[str, float]


class SkuPlanOut(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    category: str
    status: str
    on_hand: int
    on_order: int
    weekly_forecast: list[float]
    forecast_units: float
    safety_stock: float
    weeks_of_cover: float | None
    stockout_date: date | None
    order_by: date | None
    quantity: int
    amount: float
    kg_co2e: float
    impact_usd: float
    confidence: str
    backtest_wape: float | None
    vendor: VendorOptionOut | None
    alternatives: list[VendorOptionOut]
    reason: str


class RecommendationsOut(BaseModel):
    as_of: date
    weights: dict[str, float]
    emission_pressure: float
    forecast: dict[str, Any]
    plans: list[SkuPlanOut]
    budget: list[dict[str, Any]]
    plan_alignment: dict[str, Any]


class RefreshOut(BaseModel):
    created: int
    run: RecommendationsOut


@router.get("/recommendations", response_model=RecommendationsOut)
def get_recommendations(db: Db):
    """Reorder plan for every SKU: order now / upcoming / healthy / overstocked."""
    return asdict(recommendations.build_recommendations(db))


@router.post("/recommendations/refresh", response_model=RefreshOut)
def refresh_recommendations(db: Db):
    """Re-run the model and replace pending model-generated PO decisions."""
    run, created = recommendations.refresh_decisions(db)
    return RefreshOut(created=len(created), run=asdict(run))


class ForecastOut(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    last_week: date
    history: list[int]
    history_weeks: list[date]
    forecast: list[float]
    forecast_weeks: list[date]
    sigma: float
    backtest_wape: float | None
    model: str
    trained_at: datetime


@router.get("/forecast/{sku_id}", response_model=ForecastOut)
def get_sku_forecast(sku_id: int, db: Db):
    sku = db.get(Sku, sku_id)
    if sku is None:
        raise HTTPException(404, "SKU not found")
    fc = forecasting.get_forecast(db)
    f = fc.skus[sku_id]
    return ForecastOut(
        sku_id=sku.id, sku_code=sku.code, sku_name=sku.name, last_week=fc.last_week,
        history=f.history,
        history_weeks=[fc.last_week - timedelta(weeks=len(f.history) - 1 - i) for i in range(len(f.history))],
        forecast=f.weekly, forecast_weeks=fc.forecast_weeks, sigma=round(f.sigma, 2),
        backtest_wape=f.backtest_wape, model="LightGBM (Poisson, direct multi-horizon)", trained_at=fc.trained_at,
    )


@router.post("/recommendations/{sku_id}/raise", response_model=DecisionOut, status_code=201)
def raise_recommendation(sku_id: int, db: Db):
    """Create a pending PO decision for one SKU now (e.g. an upcoming reorder, raised early)."""
    if db.get(Sku, sku_id) is None:
        raise HTTPException(404, "SKU not found")
    try:
        decision = recommendations.raise_early(db, sku_id)
    except recommendations.NothingToOrder as exc:
        raise HTTPException(409, str(exc)) from exc
    return decision_out(decision)

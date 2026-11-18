"""Aging stock: hold / markdown / write-off analysis and decisions."""

from dataclasses import asdict
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import writeoffs
from ..db import get_db

router = APIRouter(tags=["aging stock"])
Db = Annotated[Session, Depends(get_db)]


class ScenarioOut(BaseModel):
    aged_sold: int
    aged_revenue: float
    newer_revenue: float
    weeks_to_clear: int | None
    holding_cost: float
    write_off_units: int
    write_off_cost: float
    cash: float


class AgingPlanOut(BaseModel):
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
    hold: ScenarioOut
    markdown: ScenarioOut
    recommendation: str
    cash_gain_vs_hold: float
    confidence: str
    reason: str
    actions: list[dict[str, Any]]


class WriteOffRunOut(BaseModel):
    as_of: date
    assumptions: dict[str, float]
    plans: list[AgingPlanOut]
    totals: dict[str, float]


class RefreshOut(BaseModel):
    created: int
    run: WriteOffRunOut


@router.get("/writeoffs", response_model=WriteOffRunOut)
def get_writeoffs(db: Db):
    """For every SKU with aged stock: hold vs. markdown cash comparison and recommended actions."""
    return asdict(writeoffs.build_writeoffs(db))


@router.post("/writeoffs/refresh", response_model=RefreshOut)
def refresh_writeoffs(db: Db):
    """Re-run the analysis and replace pending model-raised write-off/markdown decisions."""
    run, created = writeoffs.refresh_decisions(db)
    return RefreshOut(created=len(created), run=asdict(run))

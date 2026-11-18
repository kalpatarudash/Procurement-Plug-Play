"""Emissions reporting and FY targets (revenue + emissions)."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import analytics, emissions, planning
from ..config import settings
from ..db import get_db
from ..models import PlanTarget

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


# --- Emissions -------------------------------------------------------------------------------
class EmissionsPeriod(BaseModel):
    start: date
    end: date
    scope1: float
    scope2: float
    scope3: float
    total: float
    purchases_usd: float
    kg_per_revenue_usd: float | None


class EmissionsOut(BaseModel):
    label: str
    current: EmissionsPeriod
    prior_year: EmissionsPeriod
    total_change: float | None
    scope3_by_vendor: dict[str, float]
    sources: dict[str, str]


def _period(db: Session, start: date, end: date) -> tuple[EmissionsPeriod, emissions.EmissionsSummary]:
    e = emissions.emissions_summary(db, start, end)
    revenue = analytics.financial_summary(db, start, end).revenue
    return EmissionsPeriod(
        start=start, end=end, scope1=round(e.scope1, 1), scope2=round(e.scope2, 1), scope3=round(e.scope3, 1),
        total=round(e.total, 1), purchases_usd=round(e.purchases_usd, 2),
        kg_per_revenue_usd=e.total * 1000 / revenue if revenue else None,
    ), e


@router.get("/emissions", response_model=EmissionsOut, tags=["emissions"])
def get_emissions(db: Db, view: Literal["YTD", "QTD", "MTD"] = "YTD", start: date | None = None, end: date | None = None):
    """Scope 1, 2 and 3 (purchased goods) emissions in tonnes CO2e."""
    if start and end:
        if start > end:
            raise HTTPException(422, "start must be on or before end")
        label = "Custom range"
    else:
        start, end = analytics.period_for_view(view, settings.as_of)
        label = view
    current, detail = _period(db, start, end)
    prior, _ = _period(db, analytics.shift_year(start, -1), analytics.shift_year(end, -1))
    return EmissionsOut(
        label=label,
        current=current,
        prior_year=prior,
        total_change=current.total / prior.total - 1 if prior.total else None,
        scope3_by_vendor={k: round(v, 1) for k, v in sorted(detail.scope3_by_vendor.items(), key=lambda kv: -kv[1])},
        sources=emissions.SOURCES,
    )


class IndustryFactorOut(BaseModel):
    naics_code: str
    title: str
    kg_co2e_per_usd: float


@router.get("/emission-factors/{naics_code}", response_model=IndustryFactorOut, tags=["emissions"])
def get_emission_factor(naics_code: str):
    """Look up the EPA supply-chain factor for a 2017 NAICS-6 code."""
    try:
        f = emissions.industry_factor(naics_code)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return IndustryFactorOut(naics_code=f.naics_code, title=f.title, kg_co2e_per_usd=f.kg_co2e_per_usd)


# --- Plan / targets ----------------------------------------------------------------------------
class TargetsIn(BaseModel):
    revenue_target: float = Field(gt=0)
    emission_reduction_pct: float = Field(ge=0, le=90)
    updated_by: str = Field(min_length=1, max_length=120)


class SavedTargets(BaseModel):
    revenue_target: float
    emission_reduction_pct: float
    updated_at: datetime
    updated_by: str


class PlanOut(BaseModel):
    saved: SavedTargets | None
    analysis: planning.PlanAnalysis


def _saved(db: Session) -> PlanTarget | None:
    fiscal_year, _, _ = planning.plan_fiscal_year(settings.as_of)
    return db.get(PlanTarget, fiscal_year)


def _plan_out(db: Session, revenue_target: float, reduction_pct: float) -> PlanOut:
    saved = _saved(db)
    return PlanOut(
        saved=SavedTargets(revenue_target=saved.revenue_target, emission_reduction_pct=saved.emission_reduction_pct,
                           updated_at=saved.updated_at, updated_by=saved.updated_by) if saved else None,
        analysis=planning.build_plan(db, revenue_target, reduction_pct),
    )


def _check_revenue(db: Session, revenue_target: float) -> None:
    start, end = planning.baseline_window(settings.as_of)
    baseline = analytics.financial_summary(db, start, end).revenue
    if baseline and revenue_target > baseline * 10:
        raise HTTPException(422, "Revenue target is more than 10× the last 12 months' revenue.")


@router.get("/plan", response_model=PlanOut, tags=["planning"])
def get_plan(
    db: Db,
    revenue_target: float | None = Query(None, gt=0, description="Preview a target without saving it."),
    emission_reduction_pct: float | None = Query(None, ge=0, le=90),
):
    """Plan for next fiscal year: saved targets (or defaults), overridable for live what-if previews."""
    saved = _saved(db)
    default_revenue, default_pct = planning.default_targets(db)
    revenue = revenue_target or (saved.revenue_target if saved else default_revenue)
    pct = emission_reduction_pct if emission_reduction_pct is not None else (saved.emission_reduction_pct if saved else default_pct)
    _check_revenue(db, revenue)
    return _plan_out(db, revenue, pct)


@router.put("/plan", response_model=PlanOut, tags=["planning"])
def save_plan(body: TargetsIn, db: Db):
    _check_revenue(db, body.revenue_target)
    fiscal_year, _, _ = planning.plan_fiscal_year(settings.as_of)
    target = db.get(PlanTarget, fiscal_year) or PlanTarget(fiscal_year=fiscal_year)
    target.revenue_target = body.revenue_target
    target.emission_reduction_pct = body.emission_reduction_pct
    target.updated_by = body.updated_by
    target.updated_at = datetime.now(UTC)
    db.add(target)
    db.commit()
    return _plan_out(db, body.revenue_target, body.emission_reduction_pct)

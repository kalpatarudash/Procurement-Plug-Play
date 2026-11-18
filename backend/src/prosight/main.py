from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, select

from . import runtime
from .api import actions, imports, planning, recommendations, reporting, writeoffs
from .db import Base, SessionLocal, engine
from .forecasting import NotEnoughHistory
from .models import Category
from .seed import seed


def _check_schema() -> None:
    """Fail fast with a clear message if the database predates a model change."""
    existing = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not existing.has_table(table.name):
            continue
        have = {c["name"] for c in existing.get_columns(table.name)}
        missing = {c.name for c in table.columns} - have
        if missing:
            raise RuntimeError(
                f"Database is out of date (table '{table.name}' lacks {', '.join(sorted(missing))}). "
                "Rebuild the demo database with: uv run python -m prosight.seed"
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_schema()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        runtime.apply(db)  # an imported dataset carries its own as-of date, currency and fiscal year
        if db.scalar(select(Category.id).limit(1)) is None:
            seed(db)
    yield


app = FastAPI(
    title="Pro-sight API",
    version="0.1.0",
    description="Procurement FP&A backend: ERP connectors and Excel/CSV import, financials, inventory, planning, "
    "LightGBM-driven PO recommendations, and admin-approved decisions posted to the account book of record.",
    lifespan=lifespan,
)


@app.exception_handler(NotEnoughHistory)
async def not_enough_history(request: Request, exc: NotEnoughHistory):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(reporting.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(planning.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(writeoffs.router, prefix="/api")
app.include_router(imports.router, prefix="/api")

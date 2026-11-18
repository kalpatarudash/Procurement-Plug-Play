from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import inspect, select

from .api import actions, planning, recommendations, reporting
from .db import Base, SessionLocal, engine
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
        if db.scalar(select(Category.id).limit(1)) is None:
            seed(db)
    yield


app = FastAPI(
    title="Pro-sight API",
    version="0.1.0",
    description="Procurement FP&A backend: ERP connectors, financials, inventory, planning, "
    "LightGBM-driven PO recommendations, and admin-approved decisions posted to the account book of record.",
    lifespan=lifespan,
)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(reporting.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(planning.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")

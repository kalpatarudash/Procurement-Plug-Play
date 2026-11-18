from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from .api import actions, planning, reporting
from .db import Base, SessionLocal, engine
from .models import Category
from .seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(Category.id).limit(1)) is None:
            seed(db)
    yield


app = FastAPI(
    title="Pro-sight API",
    version="0.1.0",
    description="Procurement FP&A backend: ERP connectors, financials, inventory, "
    "and admin-approved decisions posted to the account book of record.",
    lifespan=lifespan,
)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(reporting.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(planning.router, prefix="/api")

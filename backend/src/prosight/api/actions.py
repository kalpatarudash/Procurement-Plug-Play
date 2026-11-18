"""Write endpoints: decisions (approve/reject → PO + ABOR posting), PO receipt, connectors, routing."""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors, decisions
from ..connectors.base import ConnectorError
from ..db import get_db
from ..models import Decision, PurchaseOrder
from .reporting import JournalEntryOut, PurchaseOrderOut, entry_out, po_out

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


# --- Decisions -------------------------------------------------------------------------------
class DecisionCreate(BaseModel):
    kind: Literal["purchase_order", "write_off"]
    title: str = Field(min_length=1, max_length=200)
    reason: str = ""
    impact_usd: float = 0.0
    payload: dict[str, Any] = Field(
        description="purchase_order: {sku_id, vendor_id, quantity, unit_cost}; write_off: {sku_id, quantity}"
    )


class DecisionOut(BaseModel):
    id: int
    kind: str
    status: str
    title: str
    reason: str
    impact_usd: float
    source: str
    payload: dict[str, Any]
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    note: str | None
    last_error: str | None
    purchase_order: PurchaseOrderOut | None
    journal_entry: JournalEntryOut | None


class ApproveIn(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)


class RejectIn(BaseModel):
    rejected_by: str = Field(min_length=1, max_length=120)
    note: str | None = None


def decision_out(d: Decision) -> DecisionOut:
    return DecisionOut(
        id=d.id,
        kind=d.kind,
        status=d.status,
        title=d.title,
        reason=d.reason,
        impact_usd=d.impact_usd,
        source=d.source,
        payload=d.payload,
        created_at=d.created_at,
        decided_at=d.decided_at,
        decided_by=d.decided_by,
        note=d.note,
        last_error=d.last_error,
        purchase_order=po_out(d.purchase_order) if d.purchase_order else None,
        journal_entry=entry_out(d.journal_entry) if d.journal_entry else None,
    )


def _get_decision(db: Session, decision_id: int) -> Decision:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    return decision


@router.get("/decisions", response_model=list[DecisionOut], tags=["decisions"])
def list_decisions(db: Db, status: Literal["pending", "approved", "rejected"] | None = None):
    query = select(Decision).order_by(Decision.created_at.desc(), Decision.id.desc())
    if status:
        query = query.where(Decision.status == status)
    return [decision_out(d) for d in db.scalars(query)]


@router.post("/decisions", response_model=DecisionOut, status_code=201, tags=["decisions"])
def create_decision(body: DecisionCreate, db: Db):
    try:
        payload = decisions.validate_payload(db, body.kind, body.payload)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    except decisions.DecisionError as exc:
        raise HTTPException(422, str(exc)) from exc
    decision = Decision(
        kind=body.kind,
        status="pending",
        title=body.title,
        reason=body.reason,
        impact_usd=body.impact_usd,
        source="manual",
        payload=payload,
        created_at=datetime.now(UTC),
    )
    db.add(decision)
    db.commit()
    return decision_out(decision)


@router.post("/decisions/{decision_id}/approve", response_model=DecisionOut, tags=["decisions"])
def approve_decision(decision_id: int, body: ApproveIn, db: Db):
    """Approve, execute (create the PO / write off the stock), and post to the ABOR."""
    decision = _get_decision(db, decision_id)
    try:
        decisions.approve(db, decision, body.approved_by)
    except decisions.DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(502, f"Not posted, decision is still pending: {exc}") from exc
    return decision_out(decision)


@router.post("/decisions/{decision_id}/reject", response_model=DecisionOut, tags=["decisions"])
def reject_decision(decision_id: int, body: RejectIn, db: Db):
    decision = _get_decision(db, decision_id)
    try:
        decisions.reject(db, decision, body.rejected_by, body.note)
    except decisions.DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return decision_out(decision)


@router.post("/purchase-orders/{po_id}/receive", response_model=PurchaseOrderOut, tags=["purchase orders"])
def receive_purchase_order(po_id: int, db: Db):
    """Record goods receipt: adds stock and posts the receipt to the ABOR."""
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "Purchase order not found")
    try:
        decisions.receive_purchase_order(db, po)
    except decisions.DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(502, f"Not posted: {exc}") from exc
    return po_out(po)


# --- Connectors & routing ---------------------------------------------------------------------
class ConnectorOut(BaseModel):
    id: str
    name: str
    kind: str
    description: str
    capabilities: list[str]
    configured: bool
    setup_hint: str | None
    connected: bool
    is_abor: bool
    is_po_target: bool


class RoutingIn(BaseModel):
    abor_connector: str | None = None
    po_connector: str | None = None


class RoutingOut(BaseModel):
    abor_connector: str
    po_connector: str


def _connectors_out(db: Session) -> list[ConnectorOut]:
    abor, po_target = connectors.route(db, "abor_connector"), connectors.route(db, "po_connector")
    return [
        ConnectorOut(
            id=c.id,
            name=c.name,
            kind=c.kind,
            description=c.description,
            capabilities=sorted(c.capabilities),
            configured=c.is_configured(),
            setup_hint=c.missing_config_hint(),
            connected=connectors.is_connected(db, c.id),
            is_abor=c.id == abor,
            is_po_target=c.id == po_target,
        )
        for c in connectors.all_connectors(db)
    ]


def _get_connector(db: Session, connector_id: str):
    connector = connectors.get_connector(db, connector_id)
    if connector is None:
        raise HTTPException(404, "Connector not found")
    return connector


@router.get("/connectors", response_model=list[ConnectorOut], tags=["connectors"])
def list_connectors(db: Db):
    return _connectors_out(db)


@router.post("/connectors/{connector_id}/connect", response_model=list[ConnectorOut], tags=["connectors"])
def connect(connector_id: str, db: Db):
    try:
        connectors.set_connected(db, _get_connector(db, connector_id), True)
    except ConnectorError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _connectors_out(db)


@router.post("/connectors/{connector_id}/disconnect", response_model=list[ConnectorOut], tags=["connectors"])
def disconnect(connector_id: str, db: Db):
    connector = _get_connector(db, connector_id)
    if connector_id in (connectors.route(db, "abor_connector"), connectors.route(db, "po_connector")):
        raise HTTPException(409, f"{connector.name} receives postings. Route them to another system first.")
    connectors.set_connected(db, connector, False)
    db.commit()
    return _connectors_out(db)


@router.get("/settings/routing", response_model=RoutingOut, tags=["connectors"])
def get_routing(db: Db):
    return RoutingOut(abor_connector=connectors.route(db, "abor_connector"), po_connector=connectors.route(db, "po_connector"))


@router.put("/settings/routing", response_model=RoutingOut, tags=["connectors"])
def set_routing(body: RoutingIn, db: Db):
    """Choose which system is the account book of record and which receives new POs."""
    try:
        for key in ("abor_connector", "po_connector"):
            if value := getattr(body, key):
                connectors.set_route(db, key, value)
    except ConnectorError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return get_routing(db)

"""Admin approval workflow: approving a decision executes it and posts it to the ABOR."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import connectors, ledger
from .config import settings
from .connectors.base import ConnectorError, PurchaseOrderPayload
from .models import Decision, InventoryLot, JournalEntry, PurchaseOrder, Sku, Vendor


class DecisionError(Exception):
    """The decision cannot be carried out as requested (bad state or bad payload)."""


class PurchaseOrderProposal(BaseModel):
    sku_id: int
    vendor_id: int
    quantity: int = Field(gt=0)
    unit_cost: float = Field(gt=0)


class WriteOffProposal(BaseModel):
    sku_id: int
    quantity: int = Field(gt=0)


PROPOSALS: dict[str, type[BaseModel]] = {"purchase_order": PurchaseOrderProposal, "write_off": WriteOffProposal}


def validate_payload(db: Session, kind: str, payload: dict) -> dict:
    proposal = PROPOSALS[kind].model_validate(payload)
    sku = db.get(Sku, proposal.sku_id)
    if sku is None:
        raise DecisionError(f"SKU {proposal.sku_id} does not exist.")
    if isinstance(proposal, PurchaseOrderProposal) and db.get(Vendor, proposal.vendor_id) is None:
        raise DecisionError(f"Vendor {proposal.vendor_id} does not exist.")
    if isinstance(proposal, WriteOffProposal) and proposal.quantity > sku.on_hand:
        raise DecisionError(f"Only {sku.on_hand} units of {sku.code} are on hand.")
    return proposal.model_dump()


def _next_po_number(db: Session) -> str:
    count = db.scalar(select(func.count()).select_from(PurchaseOrder)) or 0
    return f"PO-{settings.as_of.year}-{count + 1:04d}"


def _execute_purchase_order(db: Session, decision: Decision) -> None:
    proposal = PurchaseOrderProposal.model_validate(decision.payload)
    sku = db.get(Sku, proposal.sku_id)
    vendor = db.get(Vendor, proposal.vendor_id)
    if sku is None or vendor is None:
        raise DecisionError("The SKU or vendor on this decision no longer exists.")

    issued = settings.as_of
    po = PurchaseOrder(
        number=_next_po_number(db),
        vendor_id=vendor.id,
        sku_id=sku.id,
        quantity=proposal.quantity,
        unit_cost=proposal.unit_cost,
        currency=settings.currency,
        status="open",
        issued_date=issued,
        expected_date=issued + timedelta(days=vendor.lead_time_days),
    )
    target = connectors.routed_connector(db, "po_connector")
    po.external_system = target.id
    po.external_ref = target.create_purchase_order(
        PurchaseOrderPayload(
            po.number, vendor.name, sku.code, po.quantity, po.unit_cost, po.currency, po.issued_date, po.expected_date
        )
    )
    db.add(po)
    db.flush()

    decision.purchase_order = po
    decision.journal_entry = ledger.post_entry(
        db,
        entry_date=issued,
        memo=f"Commitment for {po.number}: {po.quantity} × {sku.code} from {vendor.name}",
        ledger="memo",
        source_type="po_commitment",
        source_id=po.id,
        lines=[ledger.Line("9100", debit=po.amount), ledger.Line("9200", credit=po.amount)],
    )


def _execute_write_off(db: Session, decision: Decision) -> None:
    proposal = WriteOffProposal.model_validate(decision.payload)
    sku = db.get(Sku, proposal.sku_id)
    if sku is None:
        raise DecisionError("The SKU on this decision no longer exists.")
    if proposal.quantity > sku.on_hand:
        raise DecisionError(f"Only {sku.on_hand} units of {sku.code} are on hand now.")

    # Write off the oldest stock first.
    remaining, cost = proposal.quantity, 0.0
    for lot in list(sku.lots):
        if remaining == 0:
            break
        take = min(lot.quantity, remaining)
        cost += take * lot.unit_cost
        lot.quantity -= take
        remaining -= take
        if lot.quantity == 0:
            sku.lots.remove(lot)  # delete-orphan cascade removes the row
    cost = round(cost, 2)

    decision.journal_entry = ledger.post_entry(
        db,
        entry_date=settings.as_of,
        memo=f"Write-off of {proposal.quantity} × {sku.code} {sku.name}",
        ledger="financial",
        source_type="write_off",
        source_id=decision.id,
        lines=[ledger.Line("5150", debit=cost), ledger.Line("1300", credit=cost)],
    )


def approve(db: Session, decision: Decision, approved_by: str) -> Decision:
    if decision.status != "pending":
        raise DecisionError(f"This decision is already {decision.status}.")
    decision_id = decision.id
    try:
        if decision.kind == "purchase_order":
            _execute_purchase_order(db, decision)
        else:
            _execute_write_off(db, decision)
        decision.status = "approved"
        decision.decided_at = datetime.now(UTC)
        decision.decided_by = approved_by
        decision.last_error = None
        db.commit()
    except ConnectorError as exc:
        # Nothing was posted; keep the decision pending and record why so the admin can retry.
        db.rollback()
        failed = db.get(Decision, decision_id)
        failed.last_error = str(exc)
        db.commit()
        raise
    except Exception:
        db.rollback()
        raise
    return decision


def reject(db: Session, decision: Decision, rejected_by: str, note: str | None) -> Decision:
    if decision.status != "pending":
        raise DecisionError(f"This decision is already {decision.status}.")
    decision.status = "rejected"
    decision.decided_at = datetime.now(UTC)
    decision.decided_by = rejected_by
    decision.note = note
    db.commit()
    return decision


def receive_purchase_order(db: Session, po: PurchaseOrder) -> PurchaseOrder:
    """Goods arrived: add stock, post the inventory receipt, and release the commitment."""
    if po.status != "open":
        raise DecisionError(f"{po.number} is already {po.status}.")
    try:
        received = settings.as_of
        po.status = "received"
        po.received_date = received
        db.add(InventoryLot(sku_id=po.sku_id, received_date=received, quantity=po.quantity, unit_cost=po.unit_cost))
        ledger.post_entry(
            db,
            entry_date=received,
            memo=f"Goods receipt for {po.number}",
            ledger="financial",
            source_type="po_receipt",
            source_id=po.id,
            lines=[ledger.Line("1300", debit=po.amount), ledger.Line("2100", credit=po.amount)],
        )
        # POs imported from the ERP's history were never committed through Pro-sight.
        committed = db.scalar(
            select(JournalEntry.id).where(JournalEntry.source_type == "po_commitment", JournalEntry.source_id == po.id)
        )
        if committed:
            ledger.post_entry(
                db,
                entry_date=received,
                memo=f"Release commitment for {po.number}",
                ledger="memo",
                source_type="po_commitment_release",
                source_id=po.id,
                lines=[ledger.Line("9200", debit=po.amount), ledger.Line("9100", credit=po.amount)],
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return po

"""Posting to the account book of record (ABOR).

Accounting treatment:
  Stock write-off   Dr 5150 Inventory write-off expense / Cr 1300 Inventory           (financial)
  Markdown below    Dr 5160 Inventory write-down (NRV) / Cr 1300 Inventory            (financial)
  cost              for (cost − clearance price net of selling costs) × units.
  PO approved       Dr 9100 Purchase commitments / Cr 9200 Reserve for commitments    (memo)
                    A PO is a commitment, not a GL transaction, until goods arrive.
  PO received       Dr 1300 Inventory / Cr 2100 Goods received not invoiced           (financial)
                    plus the reversal of the memo commitment.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import connectors
from .config import settings
from .connectors.base import JournalEntryPayload, JournalLinePayload
from .models import JournalEntry, JournalLine

ACCOUNTS = {
    "1300": "Inventory",
    "2100": "Goods received not invoiced",
    "5150": "Inventory write-off expense",
    "5160": "Inventory write-down to net realizable value",
    "9100": "Purchase commitments (memo)",
    "9200": "Reserve for purchase commitments (memo)",
}


@dataclass(frozen=True)
class Line:
    account: str
    debit: float = 0.0
    credit: float = 0.0


def _cents(amount: float) -> int:
    return round(amount * 100)


def _next_number(db: Session, entry_date: date) -> str:
    count = db.scalar(select(func.count()).select_from(JournalEntry)) or 0
    return f"JE-{entry_date.year}-{count + 1:05d}"


def post_entry(
    db: Session,
    *,
    entry_date: date,
    memo: str,
    ledger: str,
    source_type: str,
    source_id: int,
    lines: list[Line],
) -> JournalEntry:
    """Validate a balanced entry, send it to the ABOR connector, and record it locally.

    The caller owns the transaction: if the connector raises, nothing has been committed.
    """
    if len(lines) < 2:
        raise ValueError("A journal entry needs at least two lines.")
    for line in lines:
        if line.account not in ACCOUNTS:
            raise ValueError(f"Unknown account {line.account}.")
        if (line.debit > 0) == (line.credit > 0) or line.debit < 0 or line.credit < 0:
            raise ValueError("Each line must have exactly one positive debit or credit.")
    if sum(_cents(l.debit) for l in lines) != sum(_cents(l.credit) for l in lines):
        raise ValueError("Journal entry does not balance.")

    connector = connectors.routed_connector(db, "abor_connector")
    number = _next_number(db, entry_date)
    external_ref = connector.post_journal_entry(
        JournalEntryPayload(
            number=number,
            entry_date=entry_date,
            memo=memo,
            ledger=ledger,
            currency=settings.currency,
            lines=tuple(
                JournalLinePayload(l.account, ACCOUNTS[l.account], round(l.debit, 2), round(l.credit, 2)) for l in lines
            ),
        )
    )
    entry = JournalEntry(
        number=number,
        entry_date=entry_date,
        memo=memo,
        ledger=ledger,
        source_type=source_type,
        source_id=source_id,
        currency=settings.currency,
        posted_to=connector.id,
        external_ref=external_ref,
        created_at=datetime.now(UTC),
        lines=[
            JournalLine(
                account_code=l.account,
                account_name=ACCOUNTS[l.account],
                debit_cents=_cents(l.debit),
                credit_cents=_cents(l.credit),
            )
            for l in lines
        ],
    )
    db.add(entry)
    db.flush()
    return entry

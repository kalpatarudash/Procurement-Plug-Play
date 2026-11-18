from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Sku
from .base import (
    FINANCIALS,
    JOURNAL_ENTRIES,
    PURCHASE_ORDERS,
    SKUS,
    Connector,
    FinancialSummary,
    JournalEntryPayload,
    PurchaseOrderPayload,
    SkuRecord,
)


class DemoErp(Connector):
    """Built-in ERP backed by the local database (synthetic data).

    Also acts as the built-in ledger: entries posted here are kept only in Pro-sight's own
    journal tables, so the returned reference is just the Pro-sight document number.
    """

    id = "demo"
    name = "Demo ERP (sample data)"
    kind = "Demo"
    description = "Two years of synthetic sales, stock, and purchase orders. Built-in ledger."
    capabilities = frozenset({FINANCIALS, SKUS, PURCHASE_ORDERS, JOURNAL_ENTRIES})

    def __init__(self, db: Session):
        self.db = db

    def fetch_financials(self, start: date, end: date) -> FinancialSummary:
        from .. import analytics  # imported here: analytics depends on connectors.base

        return analytics.financial_summary(self.db, start, end)

    def fetch_skus(self) -> list[SkuRecord]:
        skus = self.db.scalars(select(Sku).options(selectinload(Sku.lots), selectinload(Sku.category)))
        return [
            SkuRecord(s.code, s.name, s.category.name, s.unit_cost, s.unit_price, s.on_hand, settings.currency)
            for s in skus
        ]

    def create_purchase_order(self, po: PurchaseOrderPayload) -> str:
        return po.number

    def post_journal_entry(self, entry: JournalEntryPayload) -> str:
        return entry.number

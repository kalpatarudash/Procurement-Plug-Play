"""The plug-and-play connector contract.

Every source system (ERP, CRM, demo data) implements `Connector`. The rest of the backend only
talks to this interface, so adding a new system means writing one adapter class.

Capabilities a connector can declare:
  financials        read revenue, COGS, opex, currency
  skus              read the item master and stock on hand
  purchase_orders   create purchase orders in the system (PO routing target)
  journal_entries   post journal entries (can act as the account book of record, ABOR)
"""

from abc import ABC
from dataclasses import dataclass
from datetime import date

FINANCIALS = "financials"
SKUS = "skus"
PURCHASE_ORDERS = "purchase_orders"
JOURNAL_ENTRIES = "journal_entries"


class ConnectorError(Exception):
    """A connector could not complete a call. The message is safe to show to the user."""


class NotConfiguredError(ConnectorError):
    pass


class UnsupportedOperationError(ConnectorError):
    pass


@dataclass(frozen=True)
class FinancialSummary:
    start: date
    end: date
    currency: str
    revenue: float
    cogs: float
    operating_expenses: float
    taxes_interest: float

    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cogs

    @property
    def net_income(self) -> float:
        return self.gross_profit - self.operating_expenses - self.taxes_interest


@dataclass(frozen=True)
class SkuRecord:
    code: str
    name: str
    category: str
    unit_cost: float
    unit_price: float
    on_hand: int
    currency: str


@dataclass(frozen=True)
class PurchaseOrderPayload:
    number: str
    vendor_name: str
    sku_code: str
    quantity: int
    unit_cost: float
    currency: str
    issued_date: date
    expected_date: date


@dataclass(frozen=True)
class JournalLinePayload:
    account_code: str
    account_name: str
    debit: float
    credit: float


@dataclass(frozen=True)
class JournalEntryPayload:
    number: str  # also used as the idempotency key in the target system
    entry_date: date
    memo: str
    ledger: str  # financial | memo
    currency: str
    lines: tuple[JournalLinePayload, ...]


class Connector(ABC):
    id: str
    name: str
    kind: str  # ERP | CRM | Demo
    description: str
    capabilities: frozenset[str] = frozenset()

    def is_configured(self) -> bool:
        return True

    def missing_config_hint(self) -> str | None:
        """What the user must set before this connector can connect, or None when configured."""
        return None

    def require_configured(self) -> None:
        if not self.is_configured():
            raise NotConfiguredError(f"{self.name} is not configured. {self.missing_config_hint() or ''}".strip())

    def _unsupported(self, what: str) -> UnsupportedOperationError:
        return UnsupportedOperationError(f"{self.name} does not support {what}.")

    # --- Reads -----------------------------------------------------------------------------
    def fetch_financials(self, start: date, end: date) -> FinancialSummary:
        raise self._unsupported("reading financials")

    def fetch_skus(self) -> list[SkuRecord]:
        raise self._unsupported("reading SKUs")

    # --- Writes (used when an admin approves a decision) ----------------------------------------
    def create_purchase_order(self, po: PurchaseOrderPayload) -> str:
        """Create the PO in the target system and return its id there."""
        raise self._unsupported("creating purchase orders")

    def post_journal_entry(self, entry: JournalEntryPayload) -> str:
        """Post the entry to the account book of record and return its document id there."""
        raise self._unsupported("posting journal entries")

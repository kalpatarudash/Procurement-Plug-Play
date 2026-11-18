"""Database tables.

Money is stored as float dollars for analytics tables. Journal lines use integer cents so
debits and credits balance exactly.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    skus: Mapped[list["Sku"]] = relationship(back_populates="category")


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    on_time_rate: Mapped[float]
    lead_time_days: Mapped[int]
    # 1.0 = category benchmark price; 1.07 = 7% above benchmark.
    price_index: Mapped[float]
    # 2017 NAICS code; selects the EPA supply-chain emission factor for the vendor's industry.
    naics_code: Mapped[str] = mapped_column(String(12))
    # Vendor vs. industry-average emission intensity (supplier disclosure): 1.0 = average, 0.82 = 18% lower.
    emission_intensity_index: Mapped[float] = mapped_column(default=1.0)


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))  # preferred vendor
    unit_cost: Mapped[float]
    unit_price: Mapped[float]

    category: Mapped[Category] = relationship(back_populates="skus")
    vendor: Mapped[Vendor] = relationship()
    lots: Mapped[list["InventoryLot"]] = relationship(
        back_populates="sku", order_by="InventoryLot.received_date", cascade="all, delete-orphan"
    )

    @property
    def on_hand(self) -> int:
        return sum(lot.quantity for lot in self.lots)


class InventoryLot(Base):
    """Stock on hand, by receipt date (FIFO). Drives aging and write-offs."""

    __tablename__ = "inventory_lots"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), index=True)
    received_date: Mapped[date]
    quantity: Mapped[int]
    unit_cost: Mapped[float]
    sku: Mapped[Sku] = relationship(back_populates="lots")


class SalesWeek(Base):
    __tablename__ = "sales_weeks"
    __table_args__ = (UniqueConstraint("sku_id", "week_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), index=True)
    week_start: Mapped[date] = mapped_column(index=True)
    units: Mapped[int]
    revenue: Mapped[float]
    cogs: Mapped[float]
    sku: Mapped[Sku] = relationship()


class OperatingExpense(Base):
    __tablename__ = "operating_expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(unique=True)  # first day of the month
    opex: Mapped[float]
    taxes_interest: Mapped[float]


class EnergyUsage(Base):
    """Monthly activity data for Scope 1 (fuel) and Scope 2 (electricity)."""

    __tablename__ = "energy_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(unique=True)  # first day of the month
    electricity_kwh: Mapped[float]
    natural_gas_therms: Mapped[float]
    diesel_gallons: Mapped[float]
    gasoline_gallons: Mapped[float]


class PlanTarget(Base):
    """Targets the admin sets for a fiscal year (labelled by the year it ends in)."""

    __tablename__ = "plan_targets"
    fiscal_year: Mapped[int] = mapped_column(primary_key=True)
    revenue_target: Mapped[float]
    emission_reduction_pct: Mapped[float]  # vs. the trailing-12-month baseline, e.g. 15.0
    updated_at: Mapped[datetime]
    updated_by: Mapped[str] = mapped_column(String(120))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), index=True)
    quantity: Mapped[int]
    unit_cost: Mapped[float]
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))  # open | received | cancelled
    issued_date: Mapped[date]
    expected_date: Mapped[date]
    received_date: Mapped[date | None]
    # Where the PO document lives (e.g. "sap_s4hana", "salesforce") and its id there.
    external_system: Mapped[str | None] = mapped_column(String(40))
    external_ref: Mapped[str | None] = mapped_column(String(80))

    vendor: Mapped[Vendor] = relationship()
    sku: Mapped[Sku] = relationship()

    @property
    def amount(self) -> float:
        return round(self.quantity * self.unit_cost, 2)


class JournalEntry(Base):
    """An entry posted to the account book of record (ABOR)."""

    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    entry_date: Mapped[date]
    memo: Mapped[str] = mapped_column(String(300))
    # "financial" hits the GL; "memo" is an off-balance commitment (encumbrance) entry.
    ledger: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(30))  # write_off | po_commitment | po_receipt | ...
    source_id: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    posted_to: Mapped[str] = mapped_column(String(40))  # connector id of the ABOR
    external_ref: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime]
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_code: Mapped[str] = mapped_column(String(20))
    account_name: Mapped[str] = mapped_column(String(120))
    debit_cents: Mapped[int] = mapped_column(default=0)
    credit_cents: Mapped[int] = mapped_column(default=0)
    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


class Decision(Base):
    """A recommended action (new PO or stock write-off) waiting for admin approval."""

    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # purchase_order | write_off
    status: Mapped[str] = mapped_column(String(20))  # pending | approved | rejected
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    impact_usd: Mapped[float] = mapped_column(default=0.0)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | model
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    # Last posting failure (e.g. ERP unreachable). The decision stays pending so it can be retried.
    last_error: Mapped[str | None] = mapped_column(Text)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))

    purchase_order: Mapped[PurchaseOrder | None] = relationship()
    journal_entry: Mapped[JournalEntry | None] = relationship()


class ConnectorState(Base):
    __tablename__ = "connector_states"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connected: Mapped[bool] = mapped_column(default=False)
    connected_at: Mapped[datetime | None]


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))

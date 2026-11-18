"""Bring your own data: Excel/CSV template, validation and import.

One workbook (or one CSV file per sheet, matched by file name) describes the whole dataset:

  Vendors, SKUs, Inventory, Sales, Expenses      required
  Purchase orders, Energy, Settings              optional

Uploads are always validated first (every row, every reference); nothing is written unless the
whole file is clean. Importing REPLACES the dataset: products, stock, sales, expenses, purchase
orders, energy, decisions, the built-in ledger and FY targets. Sales may be daily or weekly; they
are summed into weeks starting Monday, and the dataset's "today" (as-of date) becomes the Monday
after the last week of sales.
"""

import csv
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from . import emissions, runtime
from .config import settings
from .models import (
    Category,
    Decision,
    EnergyUsage,
    InventoryLot,
    JournalEntry,
    JournalLine,
    OperatingExpense,
    PlanTarget,
    PurchaseOrder,
    SalesWeek,
    Sku,
    Vendor,
)

MAX_ERRORS = 200
MIN_FORECAST_WEEKS = 25  # forecasting.MIN_HISTORY + BACKTEST_WEEKS + 1


# --- Column definitions ------------------------------------------------------------------------
@dataclass(frozen=True)
class Col:
    name: str
    kind: str  # text | int | number | rate | date | month | naics | status
    required: bool
    description: str
    example: Any = None
    default: Any = None


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    required: bool
    purpose: str
    columns: tuple[Col, ...]


TABLES: tuple[Table, ...] = (
    Table("vendors", "Vendors", True, "Suppliers you buy from.", (
        Col("vendor_name", "text", True, "Unique supplier name.", "Northstar Textiles"),
        Col("lead_time_days", "int", True, "Days from placing a PO to delivery.", 21),
        Col("naics_code", "naics", True, "2017 6-digit NAICS code of what they make; selects the EPA emission factor (e.g. 315280 apparel, 316210 footwear).", "315280"),
        Col("on_time_rate", "rate", False, "Share of deliveries on time: 0.96 or 96%.", 0.96, 0.9),
        Col("price_index", "number", False, "Their price vs. your benchmark cost: 1.07 = 7% above.", 1.07, 1.0),
        Col("emission_intensity_index", "number", False, "Emissions vs. industry average from supplier data: 0.82 = 18% lower.", 1.0, 1.0),
    )),
    Table("skus", "SKUs", True, "Products you sell.", (
        Col("sku_code", "text", True, "Unique product code.", "SKU-1001"),
        Col("sku_name", "text", True, "Product name.", "Rain Shell Jacket"),
        Col("category", "text", True, "Product category (new categories are created automatically).", "Spring Outerwear"),
        Col("preferred_vendor", "text", True, "vendor_name from the Vendors sheet.", "Northstar Textiles"),
        Col("unit_cost", "number", True, "Your benchmark cost per unit.", 62.4),
        Col("unit_price", "number", True, "Full selling price per unit.", 129.0),
    )),
    Table("inventory", "Inventory", True, "Stock on hand today, one row per receipt (lot). Receipt dates drive aging.", (
        Col("sku_code", "text", True, "sku_code from the SKUs sheet.", "SKU-1001"),
        Col("received_date", "date", True, "Date the stock arrived (YYYY-MM-DD).", "2025-03-14"),
        Col("quantity", "int", True, "Units on hand from this receipt.", 240),
        Col("unit_cost", "number", False, "Cost per unit of this receipt (default: the SKU's unit_cost).", 62.4),
    )),
    Table("sales", "Sales", True, "Sales history, daily or weekly. At least 25 weeks for forecasts; 2 years recommended.", (
        Col("date", "date", True, "Sale date, or the Monday of the week for weekly totals.", "2025-06-09"),
        Col("sku_code", "text", True, "sku_code from the SKUs sheet.", "SKU-1001"),
        Col("units", "int", True, "Units sold.", 18),
        Col("revenue", "number", False, "Net revenue (default: units × unit_price).", 2322.0),
        Col("cogs", "number", False, "Cost of goods sold (default: units × unit_cost).", 1123.2),
    )),
    Table("expenses", "Expenses", True, "Monthly costs below gross profit, for the income statement.", (
        Col("month", "month", True, "Any date in the month (YYYY-MM-01 works).", "2025-06-01"),
        Col("operating_expenses", "number", True, "Operating expenses for the month.", 112000),
        Col("taxes_interest", "number", False, "Taxes and interest for the month.", 13500, 0.0),
    )),
    Table("purchase_orders", "Purchase orders", False, "Open and past POs. Open POs count as stock on order; history feeds supplier spend and Scope 3 emissions.", (
        Col("po_number", "text", True, "Unique PO number.", "PO-2025-0001"),
        Col("sku_code", "text", True, "sku_code from the SKUs sheet.", "SKU-1001"),
        Col("vendor_name", "text", True, "vendor_name from the Vendors sheet.", "Northstar Textiles"),
        Col("quantity", "int", True, "Units ordered.", 300),
        Col("unit_cost", "number", True, "Agreed cost per unit.", 66.77),
        Col("issued_date", "date", True, "Date the PO was issued.", "2025-06-02"),
        Col("expected_date", "date", True, "Expected delivery date.", "2025-06-23"),
        Col("status", "status", False, "open or received.", "open", "open"),
        Col("received_date", "date", False, "Delivery date, for received POs.", None),
    )),
    Table("energy", "Energy", False, "Monthly fuel and electricity, for Scope 1 and 2 emissions.", (
        Col("month", "month", True, "Any date in the month.", "2025-06-01"),
        Col("electricity_kwh", "number", False, "Electricity used (kWh).", 100000, 0.0),
        Col("natural_gas_therms", "number", False, "Natural gas burned (therms).", 3500, 0.0),
        Col("diesel_gallons", "number", False, "Diesel used by your fleet (gallons).", 1300, 0.0),
        Col("gasoline_gallons", "number", False, "Gasoline used (gallons).", 400, 0.0),
    )),
    Table("settings", "Settings", False, "Optional: one row per setting.", (
        Col("setting", "text", True, "currency or fiscal_year_start_month.", "currency"),
        Col("value", "text", True, "e.g. USD, or 7 for a July–June fiscal year.", "USD"),
    )),
)
BY_KEY = {t.key: t for t in TABLES}
STATUSES = {"open", "received"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


_TABLE_LOOKUP = {_norm(t.title): t.key for t in TABLES} | {_norm(t.key): t.key for t in TABLES}


# --- Cell coercion -------------------------------------------------------------------------------
_EXCEL_EPOCH = date(1899, 12, 30)


def _to_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel serial date typed as a number
        return _EXCEL_EPOCH + timedelta(days=int(v))
    text = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date (use YYYY-MM-DD)")


def _to_number(v: Any, allow_percent: bool = False) -> float:
    if isinstance(v, bool):
        raise ValueError("expected a number")
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip().replace(",", "").replace("$", "")
    pct = text.endswith("%")
    if pct:
        if not allow_percent:
            raise ValueError(f"'{v}' should be a plain number, not a percentage")
        text = text[:-1]
    try:
        n = float(text)
    except ValueError:
        raise ValueError(f"'{v}' is not a number") from None
    return n / 100 if pct else n


COERCE: dict[str, Callable[[Any], Any]] = {
    "text": lambda v: str(v).strip() if not isinstance(v, float) or not v.is_integer() else str(int(v)),
    "int": lambda v: _int(v),
    "number": lambda v: _to_number(v),
    "rate": lambda v: _rate(v),
    "date": _to_date,
    "month": lambda v: _to_date(v).replace(day=1),
    "naics": lambda v: _naics(v),
    "status": lambda v: _status(v),
}


def _int(v: Any) -> int:
    n = _to_number(v)
    if not float(n).is_integer():
        raise ValueError(f"'{v}' should be a whole number")
    return int(n)


def _rate(v: Any) -> float:
    n = _to_number(v, allow_percent=True)
    if n > 1:  # 96 means 96%
        n /= 100
    if not 0 <= n <= 1:
        raise ValueError(f"'{v}' should be between 0 and 1 (or 0%–100%)")
    return n


def _naics(v: Any) -> str:
    code = COERCE["text"](v)
    if code not in emissions.epa_factors():
        raise ValueError(f"NAICS code '{code}' is not in the EPA 2017 NAICS factor set")
    return code


def _status(v: Any) -> str:
    s = str(v).strip().lower()
    if s not in STATUSES:
        raise ValueError(f"status must be open or received, not '{v}'")
    return s


# --- Reading files ---------------------------------------------------------------------------------
@dataclass
class Issue:
    sheet: str
    row: int | None
    column: str | None
    message: str


@dataclass
class Report:
    ok: bool
    errors: list[Issue]
    error_count: int
    warnings: list[str]
    summary: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict, repr=False)


class _Collector:
    def __init__(self) -> None:
        self.errors: list[Issue] = []
        self.count = 0
        self.warnings: list[str] = []

    def error(self, sheet: str, row: int | None, column: str | None, message: str) -> None:
        self.count += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(Issue(sheet, row, column, message))


def _rows_from_xlsx(content: bytes) -> dict[str, list[list[Any]]]:
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        key = _TABLE_LOOKUP.get(_norm(ws.title))
        if key:
            out[key] = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return out


def _rows_from_csv(name: str, content: bytes) -> tuple[str | None, list[list[Any]]]:
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    text = content.decode("utf-8-sig", errors="replace")
    return _TABLE_LOOKUP.get(_norm(stem)), [row for row in csv.reader(io.StringIO(text))]


def read_uploads(files: list[tuple[str, bytes]]) -> tuple[dict[str, list[list[Any]]], list[str]]:
    """Map uploaded files to tables. Returns raw rows per table and file-level problems."""
    tables: dict[str, list[list[Any]]] = {}
    problems: list[str] = []
    for name, content in files:
        lower = name.lower()
        if lower.endswith((".xlsx", ".xlsm")):
            try:
                found = _rows_from_xlsx(content)
            except Exception:
                problems.append(f"{name}: could not be read as an Excel workbook.")
                continue
            if not found:
                problems.append(f"{name}: no sheets named like the template ({', '.join(t.title for t in TABLES)}).")
            tables.update(found)
        elif lower.endswith(".csv"):
            key, rows = _rows_from_csv(name, content)
            if key is None:
                problems.append(f"{name}: name the file after a template sheet, e.g. sales.csv or purchase_orders.csv.")
            else:
                tables[key] = rows
        elif lower.endswith(".xls"):
            problems.append(f"{name}: old .xls files aren't supported; save it as .xlsx or CSV.")
        else:
            problems.append(f"{name}: upload .xlsx or .csv files.")
    return tables, problems


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_table(table: Table, rows: list[list[Any]], issues: _Collector) -> list[dict[str, Any]]:
    rows = [r for r in rows if not all(_blank(c) for c in r)]
    if not rows:
        issues.error(table.title, None, None, "Sheet is empty.")
        return []
    header = [_norm(c) if not _blank(c) else "" for c in rows[0]]
    index = {}
    for col in table.columns:
        if _norm(col.name) in header:
            index[col.name] = header.index(_norm(col.name))
        elif col.required:
            issues.error(table.title, 1, col.name, f"Missing required column '{col.name}'.")
    if any(col.required and col.name not in index for col in table.columns):
        return []

    records = []
    for offset, raw in enumerate(rows[1:], start=2):
        record: dict[str, Any] = {"_row": offset}
        bad = False
        for col in table.columns:
            value = raw[index[col.name]] if col.name in index and index[col.name] < len(raw) else None
            if _blank(value):
                if col.required:
                    issues.error(table.title, offset, col.name, "Required value is missing.")
                    bad = True
                record[col.name] = col.default
                continue
            try:
                record[col.name] = COERCE[col.kind](value)
            except ValueError as exc:
                issues.error(table.title, offset, col.name, str(exc))
                bad = True
        if not bad:
            records.append(record)
    return records


# --- Validation ------------------------------------------------------------------------------------
def validate(files: list[tuple[str, bytes]]) -> Report:
    issues = _Collector()
    raw, problems = read_uploads(files)
    for p in problems:
        issues.error("File", None, None, p)
    for table in TABLES:
        if table.required and table.key not in raw:
            issues.error(table.title, None, None, f"Missing required sheet '{table.title}'.")

    parsed = {key: _parse_table(BY_KEY[key], rows, issues) for key, rows in raw.items()}
    vendors, skus = parsed.get("vendors", []), parsed.get("skus", [])
    inventory, sales = parsed.get("inventory", []), parsed.get("sales", [])
    expenses, pos = parsed.get("expenses", []), parsed.get("purchase_orders", [])
    energy, settings_rows = parsed.get("energy", []), parsed.get("settings", [])

    def unique(rows: list[dict], key: str, sheet: str, label: str) -> dict:
        seen: dict = {}
        for r in rows:
            k = r[key].lower() if isinstance(r[key], str) else r[key]
            if k in seen:
                issues.error(sheet, r["_row"], key, f"Duplicate {label} '{r[key]}' (also on row {seen[k]['_row']}).")
            else:
                seen[k] = r
        return seen

    vendor_by = unique(vendors, "vendor_name", "Vendors", "vendor")
    sku_by = unique(skus, "sku_code", "SKUs", "SKU")
    unique(pos, "po_number", "Purchase orders", "PO number")
    unique(expenses, "month", "Expenses", "month")
    unique(energy, "month", "Energy", "month")

    for r in vendors:
        if r["lead_time_days"] <= 0:
            issues.error("Vendors", r["_row"], "lead_time_days", "Must be at least 1 day.")
        for c in ("price_index", "emission_intensity_index"):
            if r[c] <= 0:
                issues.error("Vendors", r["_row"], c, "Must be greater than 0.")
    for r in skus:
        if r["preferred_vendor"].lower() not in vendor_by:
            issues.error("SKUs", r["_row"], "preferred_vendor", f"Vendor '{r['preferred_vendor']}' is not on the Vendors sheet.")
        for c in ("unit_cost", "unit_price"):
            if r[c] <= 0:
                issues.error("SKUs", r["_row"], c, "Must be greater than 0.")
        if r["unit_price"] < r["unit_cost"]:
            issues.warnings.append(f"SKUs row {r['_row']}: {r['sku_code']} sells below cost.")

    def known_sku(sheet: str, r: dict) -> dict | None:
        sku = sku_by.get(r["sku_code"].lower())
        if sku is None:
            issues.error(sheet, r["_row"], "sku_code", f"SKU '{r['sku_code']}' is not on the SKUs sheet.")
        return sku

    # Sales → weekly buckets (Monday). The as-of date is the Monday after the last week.
    weekly: dict[tuple[str, date], list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    non_monday = False
    for r in sales:
        sku = known_sku("Sales", r)
        if sku is None:
            continue
        if r["units"] < 0:
            issues.error("Sales", r["_row"], "units", "Units can't be negative (record returns as lower sales).")
            continue
        week = r["date"] - timedelta(days=r["date"].weekday())
        non_monday |= r["date"].weekday() != 0
        bucket = weekly[(sku["sku_code"], week)]
        bucket[0] += r["units"]
        bucket[1] += r["revenue"] if r["revenue"] is not None else r["units"] * sku["unit_price"]
        bucket[2] += r["cogs"] if r["cogs"] is not None else r["units"] * sku["unit_cost"]
    weeks = sorted({w for _, w in weekly})
    as_of = weeks[-1] + timedelta(days=7) if weeks else None
    if not sales and "sales" in raw:
        issues.error("Sales", None, None, "No valid sales rows.")
    if weeks and len(weeks) < MIN_FORECAST_WEEKS:
        issues.warnings.append(
            f"Sales cover {len(weeks)} weeks. Forecasts and PO/write-off recommendations need at least "
            f"{MIN_FORECAST_WEEKS} weeks; dashboards will still work.")
    if non_monday and sales:
        last_day = max(r["date"] for r in sales)
        if last_day.weekday() != 6:
            issues.warnings.append(
                f"The last week of sales (from {weeks[-1]:%b %-d, %Y}) ends on {last_day:%a %b %-d}, not a Sunday, so it is "
                "only partly recorded and will look like a slow week. Consider removing those days.")

    for r in inventory:
        sku = known_sku("Inventory", r)
        if sku is None:
            continue
        if r["quantity"] <= 0:
            issues.error("Inventory", r["_row"], "quantity", "Must be at least 1 (leave out SKUs with no stock).")
        if as_of and r["received_date"] > as_of:
            issues.error("Inventory", r["_row"], "received_date", f"Is after the end of your sales data ({as_of}).")
        if r["unit_cost"] is None:
            r["unit_cost"] = sku["unit_cost"]

    for r in pos:
        known_sku("Purchase orders", r)
        if r["vendor_name"].lower() not in vendor_by:
            issues.error("Purchase orders", r["_row"], "vendor_name", f"Vendor '{r['vendor_name']}' is not on the Vendors sheet.")
        if r["quantity"] <= 0:
            issues.error("Purchase orders", r["_row"], "quantity", "Must be at least 1.")
        if r["expected_date"] < r["issued_date"]:
            issues.error("Purchase orders", r["_row"], "expected_date", "Is before the issued date.")
        if r["status"] == "received" and r["received_date"] is None:
            r["received_date"] = r["expected_date"]

    config: dict[str, Any] = {}
    for r in settings_rows:
        key = _norm(r["setting"])
        if key == "currency":
            if not re.fullmatch(r"[A-Za-z]{3}", r["value"]):
                issues.error("Settings", r["_row"], "value", "Currency must be a 3-letter code like USD.")
            else:
                config["currency"] = r["value"].upper()
        elif key == "fiscalyearstartmonth":
            try:
                month = int(float(r["value"]))
                assert 1 <= month <= 12
                config["fiscal_year_start_month"] = month
            except (ValueError, AssertionError):
                issues.error("Settings", r["_row"], "value", "fiscal_year_start_month must be 1–12.")
        else:
            issues.error("Settings", r["_row"], "setting", f"Unknown setting '{r['setting']}'.")

    if weeks and expenses:
        have = {r["month"] for r in expenses}
        missing = sorted({w.replace(day=1) for w in weeks} - have)
        if missing:
            issues.warnings.append(
                f"Expenses are missing for {len(missing)} month(s) with sales (e.g. {missing[0]:%b %Y}); "
                "net income will be overstated for those months.")

    summary = {
        "vendors": len(vendors),
        "skus": len(skus),
        "categories": len({r["category"] for r in skus}),
        "inventory_lots": len(inventory),
        "inventory_units": sum(r["quantity"] for r in inventory),
        "inventory_value": round(sum(r["quantity"] * (r["unit_cost"] or 0) for r in inventory), 2),
        "sales_rows": len(sales),
        "sales_weeks": len(weeks),
        "sales_from": weeks[0].isoformat() if weeks else None,
        "sales_to": (as_of - timedelta(days=1)).isoformat() if as_of else None,
        "revenue": round(sum(v[1] for v in weekly.values()), 2),
        "expense_months": len(expenses),
        "purchase_orders": len(pos),
        "open_purchase_orders": sum(1 for r in pos if r["status"] == "open"),
        "energy_months": len(energy),
        "as_of": as_of.isoformat() if as_of else None,
        "currency": config.get("currency", "USD"),
        "fiscal_year_start_month": config.get("fiscal_year_start_month", 7),
    }
    data = {"vendors": vendors, "skus": skus, "inventory": inventory, "weekly": weekly, "expenses": expenses,
            "purchase_orders": pos, "energy": energy, "config": config, "as_of": as_of}
    return Report(ok=issues.count == 0, errors=issues.errors, error_count=issues.count,
                  warnings=issues.warnings, summary=summary, data=data)


# --- Import ------------------------------------------------------------------------------------------
def replace_dataset(db: Session, report: Report) -> None:
    """Write a validated report, replacing the current dataset in one transaction."""
    if not report.ok:
        raise ValueError("Only a validated upload without errors can be imported.")
    d = report.data
    try:
        for model in (JournalLine, JournalEntry, Decision, PurchaseOrder, InventoryLot, SalesWeek, Sku,
                      Category, Vendor, OperatingExpense, EnergyUsage, PlanTarget):
            db.execute(delete(model))

        vendors = {r["vendor_name"].lower(): Vendor(
            name=r["vendor_name"], on_time_rate=r["on_time_rate"], lead_time_days=r["lead_time_days"],
            price_index=r["price_index"], naics_code=r["naics_code"],
            emission_intensity_index=r["emission_intensity_index"]) for r in d["vendors"]}
        db.add_all(vendors.values())
        categories: dict[str, Category] = {}
        skus: dict[str, Sku] = {}
        for r in d["skus"]:
            cat = categories.setdefault(r["category"].lower(), Category(name=r["category"]))
            skus[r["sku_code"].lower()] = Sku(
                code=r["sku_code"], name=r["sku_name"], category=cat, vendor=vendors[r["preferred_vendor"].lower()],
                unit_cost=r["unit_cost"], unit_price=r["unit_price"])
        db.add_all(skus.values())
        for r in d["inventory"]:
            db.add(InventoryLot(sku=skus[r["sku_code"].lower()], received_date=r["received_date"],
                                quantity=r["quantity"], unit_cost=r["unit_cost"]))
        for (code, week), (units, revenue, cogs) in d["weekly"].items():
            db.add(SalesWeek(sku=skus[code.lower()], week_start=week, units=int(units),
                             revenue=round(revenue, 2), cogs=round(cogs, 2)))
        for r in d["expenses"]:
            db.add(OperatingExpense(month=r["month"], opex=r["operating_expenses"], taxes_interest=r["taxes_interest"]))
        for r in d["energy"]:
            db.add(EnergyUsage(month=r["month"], electricity_kwh=r["electricity_kwh"],
                               natural_gas_therms=r["natural_gas_therms"], diesel_gallons=r["diesel_gallons"],
                               gasoline_gallons=r["gasoline_gallons"]))
        currency = d["config"].get("currency", "USD")
        for r in d["purchase_orders"]:
            db.add(PurchaseOrder(
                number=r["po_number"], vendor=vendors[r["vendor_name"].lower()], sku=skus[r["sku_code"].lower()],
                quantity=r["quantity"], unit_cost=r["unit_cost"], currency=currency, status=r["status"],
                issued_date=r["issued_date"], expected_date=r["expected_date"], received_date=r["received_date"],
                external_system="import", external_ref=r["po_number"]))
        runtime.store(db, {
            "as_of": d["as_of"],
            "currency": d["config"].get("currency"),
            "fiscal_year_start_month": d["config"].get("fiscal_year_start_month"),
        })
        db.commit()
    except Exception:
        db.rollback()
        raise
    runtime.apply(db)


# --- Templates -------------------------------------------------------------------------------------
def _example_rows() -> dict[str, list[list[Any]]]:
    """Small, internally consistent example for the blank template."""
    rows = {t.key: [[c.example for c in t.columns]] for t in TABLES}
    rows["vendors"].append(["Evergreen Manufacturing", 28, "315280", 0.91, 1.02, 0.82])
    rows["skus"].append(["SKU-1002", "Canvas Low-Top", "Classic Footwear", "Evergreen Manufacturing", 41.1, 79.0])
    rows["inventory"].append(["SKU-1002", "2025-05-30", 120, 41.1])
    rows["sales"] += [["2025-06-02", "SKU-1001", 21, None, None], ["2025-06-09", "SKU-1002", 30, None, None]]
    rows["expenses"].append(["2025-05-01", 108500, 13100])
    rows["settings"].append(["fiscal_year_start_month", 7])
    return rows


def _current_rows(db: Session) -> dict[str, list[list[Any]]]:
    """The current dataset in template form (also a full working example)."""
    vendors = db.scalars(select(Vendor).order_by(Vendor.name)).all()
    skus = db.scalars(select(Sku).options(selectinload(Sku.category), selectinload(Sku.vendor)).order_by(Sku.code)).all()
    code = {s.id: s.code for s in skus}
    vname = {v.id: v.name for v in vendors}
    return {
        "vendors": [[v.name, v.lead_time_days, v.naics_code, v.on_time_rate, v.price_index, v.emission_intensity_index] for v in vendors],
        "skus": [[s.code, s.name, s.category.name, s.vendor.name, s.unit_cost, s.unit_price] for s in skus],
        "inventory": [[code[l.sku_id], l.received_date, l.quantity, l.unit_cost]
                      for l in db.scalars(select(InventoryLot).order_by(InventoryLot.sku_id, InventoryLot.received_date))],
        "sales": [[w.week_start, code[w.sku_id], w.units, w.revenue, w.cogs]
                  for w in db.scalars(select(SalesWeek).order_by(SalesWeek.week_start, SalesWeek.sku_id))],
        "expenses": [[e.month, e.opex, e.taxes_interest] for e in db.scalars(select(OperatingExpense).order_by(OperatingExpense.month))],
        "purchase_orders": [[p.number, code[p.sku_id], vname[p.vendor_id], p.quantity, p.unit_cost, p.issued_date,
                             p.expected_date, p.status, p.received_date]
                            for p in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.issued_date, PurchaseOrder.id))],
        "energy": [[e.month, e.electricity_kwh, e.natural_gas_therms, e.diesel_gallons, e.gasoline_gallons]
                   for e in db.scalars(select(EnergyUsage).order_by(EnergyUsage.month))],
        "settings": [["currency", settings.currency], ["fiscal_year_start_month", settings.fiscal_year_start_month]],
    }


HEADER_FILL = PatternFill("solid", fgColor="315F55")
REQUIRED_FILL = PatternFill("solid", fgColor="E8F0DF")


def build_workbook(rows: dict[str, list[list[Any]]], sample: bool) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Instructions"
    ws["A1"] = "Pro-sight data template"
    ws["A1"].font = Font(size=16, bold=True, color="193A33")
    lines = [
        "This workbook is pre-filled with the current dataset as a complete working example." if sample
        else "Each sheet has one or two example rows. Replace them with your own data.",
        "1. Fill in Vendors, SKUs, Inventory, Sales and Expenses (required). Purchase orders, Energy and Settings are optional.",
        "2. Keep the sheet names and the column headers in row 1. Extra columns are ignored.",
        "3. Dates as YYYY-MM-DD. Sales can be daily or weekly; they're summed into weeks starting Monday.",
        "4. In Pro-sight, Inventory tab → Upload your data: choose this file, check the results, then import.",
        "   Importing REPLACES all current data, including decisions and the built-in ledger.",
        "CSV instead of Excel: save each sheet as its own CSV named after it (vendors.csv, skus.csv, purchase_orders.csv, …) and upload them together.",
    ]
    for i, line in enumerate(lines, start=3):
        ws.cell(row=i, column=1, value=line)
    r = len(lines) + 4
    for col_idx, head in enumerate(("Sheet", "Column", "Required", "Description", "Example"), start=1):
        cell = ws.cell(row=r, column=col_idx, value=head)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
    for table in TABLES:
        r += 1
        ws.cell(row=r, column=1, value=f"{table.title} ({'required' if table.required else 'optional'})").font = Font(bold=True)
        ws.cell(row=r, column=4, value=table.purpose).font = Font(italic=True)
        for col in table.columns:
            r += 1
            ws.cell(row=r, column=2, value=col.name)
            ws.cell(row=r, column=3, value="yes" if col.required else "")
            ws.cell(row=r, column=4, value=col.description + (f" Default: {col.default}." if col.default not in (None, "") else ""))
            ws.cell(row=r, column=5, value=col.example)
    for letter, width in zip("ABCDE", (26, 26, 10, 90, 22)):
        ws.column_dimensions[letter].width = width

    for table in TABLES:
        sheet = wb.create_sheet(table.title)
        for c, col in enumerate(table.columns, start=1):
            cell = sheet.cell(row=1, column=c, value=col.name)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
            sheet.column_dimensions[get_column_letter(c)].width = max(14, len(col.name) + 4)
        for values in rows.get(table.key, []):
            sheet.append(values)
        for c, col in enumerate(table.columns, start=1):
            if col.kind in ("date", "month"):
                for row in sheet.iter_rows(min_row=2, min_col=c, max_col=c):
                    row[0].number_format = "yyyy-mm-dd"
        sheet.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv_zip(rows: dict[str, list[list[Any]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in TABLES:
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow([c.name for c in table.columns])
            for values in rows.get(table.key, []):
                writer.writerow(["" if v is None else v.isoformat() if isinstance(v, date) else v for v in values])
            z.writestr(f"{table.key}.csv", text.getvalue())
    return buf.getvalue()


def template_rows(db: Session, sample: bool) -> dict[str, list[list[Any]]]:
    return _current_rows(db) if sample else _example_rows()


def import_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")

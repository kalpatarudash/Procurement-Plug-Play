import io
import zipfile
from datetime import date, timedelta

import pytest

from prosight import importer

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _small_dataset(weeks: int = 30) -> dict[str, list[list]]:
    """A tiny but complete dataset: 2 vendors, 3 SKUs, `weeks` weeks of sales ending Sun 2026-03-29."""
    end_monday = date(2026, 3, 23)
    start = end_monday - timedelta(weeks=weeks - 1)
    sales = []
    for w in range(weeks):
        monday = start + timedelta(weeks=w)
        for code, base in (("A-1", 40), ("A-2", 25), ("B-1", 10)):
            for day in range(7):  # daily rows, summed into weeks on import
                sales.append([monday + timedelta(days=day), code, base // 7 + (day % 2), None, None])
    months = sorted({(start + timedelta(days=d)).replace(day=1) for d in range(weeks * 7)})
    return {
        "vendors": [["Alpha Mills", 14, "315280", "95%", 1.0, 0.9], ["Beta Shoes", 30, "316210", 0.9, 1.05, 1.0]],
        "skus": [["A-1", "Tee", "Basics", "Alpha Mills", 8.0, 20.0], ["A-2", "Polo", "Basics", "Alpha Mills", 12.0, 30.0],
                 ["B-1", "Sneaker", "Footwear", "Beta Shoes", 40.0, 90.0]],
        "inventory": [["A-1", date(2026, 3, 1), 120, None], ["A-2", date(2025, 6, 1), 300, 11.5], ["B-1", date(2026, 2, 1), 60, None]],
        "sales": sales,
        "expenses": [[m, 20000, 2000] for m in months],
        "purchase_orders": [["PO-1", "B-1", "Beta Shoes", 50, 42.0, date(2026, 3, 20), date(2026, 4, 19), "open", None]],
        "energy": [[m, 5000, 200, 50, 20] for m in months],
        "settings": [["currency", "EUR"], ["fiscal_year_start_month", 1]],
    }


def _xlsx(rows) -> tuple[str, bytes, str]:
    return ("data.xlsx", importer.build_workbook(rows, sample=False), XLSX)


def test_templates_download(client):
    for sample in ("false", "true"):
        r = client.get("/api/import/template.xlsx", params={"sample": sample})
        assert r.status_code == 200 and r.headers["content-type"] == XLSX
        assert "attachment" in r.headers["content-disposition"]
    z = client.get("/api/import/template.zip")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert {"vendors.csv", "skus.csv", "sales.csv", "purchase_orders.csv"} <= set(names)


def test_sample_data_round_trips(client):
    content = client.get("/api/import/template.xlsx", params={"sample": "true"}).content
    report = client.post("/api/import/validate", files=[("files", ("sample.xlsx", content, XLSX))]).json()
    assert report["ok"] and report["error_count"] == 0
    assert report["summary"]["as_of"] == "2025-06-16"
    assert report["summary"]["inventory_value"] == pytest.approx(client.get("/api/inventory").json()["total_value"], abs=0.05)


def test_import_replaces_dataset_and_settings(client):
    client.post("/api/recommendations/refresh")  # leaves pending decisions that the import must clear
    r = client.post("/api/import", files=[("files", _xlsx(_small_dataset()))])
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["imported"] and body["summary"]["sales_weeks"] == 30

    ds = client.get("/api/dataset").json()
    assert ds == {"source": "import", "as_of": "2026-03-30", "currency": "EUR", "fiscal_year_start_month": 1}
    inv = client.get("/api/inventory").json()
    assert inv["sku_count"] == 3
    assert inv["total_value"] == pytest.approx(120 * 8 + 300 * 11.5 + 60 * 40)
    assert inv["aged_value"] == pytest.approx(300 * 11.5)  # the June 2025 lot is over 180 days old
    assert client.get("/api/decisions").json() == []
    assert len(client.get("/api/purchase-orders", params={"status": "open"}).json()) == 1
    fin = client.get("/api/financials", params={"view": "YTD"}).json()  # Jan-start fiscal year
    assert fin["current"]["start"] == "2026-01-01" and fin["current"]["revenue"] > 0
    run = client.get("/api/recommendations").json()  # 30 weeks is enough to forecast
    assert len(run["plans"]) == 3


def test_errors_are_reported_by_row_and_nothing_changes(client):
    rows = _small_dataset()
    rows["skus"][2][3] = "Gamma Ltd"  # unknown vendor
    rows["vendors"][0][2] = "999999"  # not a NAICS code
    rows["sales"][5][0] = "next tuesday"
    before = client.get("/api/inventory").json()["total_value"]
    r = client.post("/api/import", files=[("files", _xlsx(rows))])
    assert r.status_code == 422
    report = r.json()["detail"]
    found = {(e["sheet"], e["column"]) for e in report["errors"]}
    assert ("SKUs", "preferred_vendor") in found
    assert ("Vendors", "naics_code") in found
    assert ("Sales", "date") in found
    assert all(e["row"] for e in report["errors"])
    assert client.get("/api/inventory").json()["total_value"] == before
    assert client.get("/api/dataset").json()["source"] == "demo"


def test_missing_required_sheet(client):
    rows = _small_dataset()
    content = importer.build_workbook(rows, sample=False)
    # Rebuild without the Expenses sheet.
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    del wb["Expenses"]
    buf = io.BytesIO()
    wb.save(buf)
    report = client.post("/api/import/validate", files=[("files", ("x.xlsx", buf.getvalue(), XLSX))]).json()
    assert not report["ok"]
    assert any("Expenses" in e["message"] for e in report["errors"])


def test_csv_files_import(client):
    z = zipfile.ZipFile(io.BytesIO(importer.build_csv_zip(_small_dataset())))
    files = [("files", (n, z.read(n), "text/csv")) for n in z.namelist()]
    r = client.post("/api/import", files=files)
    assert r.status_code == 200 and r.json()["summary"]["skus"] == 3
    bad = client.post("/api/import/validate", files=[("files", ("stuff.csv", b"a,b\n1,2\n", "text/csv"))]).json()
    assert any("name the file after a template sheet" in e["message"] for e in bad["errors"])


def test_short_history_imports_but_recommendations_explain(client):
    r = client.post("/api/import", files=[("files", _xlsx(_small_dataset(weeks=10)))])
    assert r.status_code == 200
    assert any("25 weeks" in w for w in r.json()["warnings"])
    assert client.get("/api/inventory").status_code == 200
    rec = client.get("/api/recommendations")
    assert rec.status_code == 409 and "weeks of sales history" in rec.json()["detail"]


def test_reset_demo_restores_everything(client):
    client.post("/api/import", files=[("files", _xlsx(_small_dataset()))])
    ds = client.post("/api/import/reset-demo").json()
    assert ds["source"] == "demo" and ds["as_of"] == "2025-06-16" and ds["currency"] == "USD"
    assert client.get("/api/inventory").json()["sku_count"] == 36

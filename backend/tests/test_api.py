import pytest


def _sku(client, category):
    return next(s for s in client.get("/api/inventory/skus").json() if s["category"] == category)


def _vendor(client, name="Evergreen Manufacturing"):
    return next(v for v in client.get("/api/vendors").json() if v["name"] == name)


def _balances(entry):
    return sum(l["debit"] for l in entry["lines"]), sum(l["credit"] for l in entry["lines"])


def test_financials_ytd_matches_seed_target(client):
    body = client.get("/api/financials", params={"view": "YTD"}).json()
    assert abs(body["current"]["revenue"] - 4_820_000) < 5_000
    cur = body["current"]
    assert abs(cur["net_income"] - (cur["revenue"] - cur["cogs"] - cur["operating_expenses"] - cur["taxes_interest"])) < 0.05
    assert body["revenue_growth"] > 0
    assert body["monthly_revenue"]


def test_financials_custom_range_and_validation(client):
    ok = client.get("/api/financials", params={"start": "2025-01-01", "end": "2025-03-31"})
    assert ok.status_code == 200 and ok.json()["label"] == "Custom range"
    assert client.get("/api/financials", params={"start": "2025-04-01", "end": "2025-03-01"}).status_code == 422


def test_inventory_flags_overstock_and_aging(client):
    body = client.get("/api/inventory").json()
    statuses = {c["name"]: c["status"] for c in body["categories"]}
    assert statuses["Spring Outerwear"] == "Overstocked"
    assert statuses["Accessories"] == "Aging"


def test_items_list_vendors_and_vendors_summarise_items(client):
    skus = client.get("/api/inventory/skus").json()
    vendors = {v["name"]: v for v in client.get("/api/vendors").json()}
    for s in skus:
        assert s["vendor"] in vendors and s["vendor"] not in s["alternative_vendors"]
        assert set(s["alternative_vendors"]) <= set(vendors)
        assert s["status"] in {"Stockout risk", "Aging", "Overstocked", "Healthy"}
    assert sum(v["preferred_sku_count"] for v in vendors.values()) == len(skus)
    open_pos = client.get("/api/purchase-orders", params={"status": "open"}).json()
    assert sum(v["open_po_count"] for v in vendors.values()) == len(open_pos)
    assert sum(v["open_po_value"] for v in vendors.values()) == pytest.approx(sum(p["amount"] for p in open_pos), abs=0.05)
    for s in skus:
        assert s["category"] in vendors[s["vendor"]]["categories"]


def test_write_off_approval_posts_balanced_entry_and_reduces_oldest_stock(client):
    sku = _sku(client, "Accessories")
    qty = sku["aged_units"]
    created = client.post("/api/decisions", json={
        "kind": "write_off", "title": f"Write off aged {sku['code']}", "payload": {"sku_id": sku["id"], "quantity": qty},
    })
    assert created.status_code == 201

    approved = client.post(f"/api/decisions/{created.json()['id']}/approve", json={"approved_by": "Olivia Hayes"})
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    entry = body["journal_entry"]
    assert entry["ledger"] == "financial" and entry["posted_to"] == "demo"
    debit, credit = _balances(entry)
    assert debit == credit == round(qty * sku["unit_cost"], 2)
    assert {l["account_code"] for l in entry["lines"]} == {"5150", "1300"}

    after = next(s for s in client.get("/api/inventory/skus").json() if s["id"] == sku["id"])
    assert after["on_hand"] == sku["on_hand"] - qty
    assert after["aged_units"] == 0  # oldest lots written off first

    again = client.post(f"/api/decisions/{body['id']}/approve", json={"approved_by": "Olivia Hayes"})
    assert again.status_code == 409


def test_write_off_cannot_exceed_stock(client):
    sku = _sku(client, "Accessories")
    r = client.post("/api/decisions", json={
        "kind": "write_off", "title": "Too much", "payload": {"sku_id": sku["id"], "quantity": sku["on_hand"] + 1},
    })
    assert r.status_code == 422


def test_po_approval_creates_po_and_commitment_then_receipt_posts_inventory(client):
    sku, vendor = _sku(client, "Classic Footwear"), _vendor(client)
    open_before = len(client.get("/api/purchase-orders", params={"status": "open"}).json())
    created = client.post("/api/decisions", json={
        "kind": "purchase_order", "title": "Reorder", "payload": {
            "sku_id": sku["id"], "vendor_id": vendor["id"], "quantity": 100, "unit_cost": 40.0},
    }).json()

    body = client.post(f"/api/decisions/{created['id']}/approve", json={"approved_by": "Olivia Hayes"}).json()
    po = body["purchase_order"]
    assert po["status"] == "open" and po["amount"] == 4000.0 and po["external_system"] == "demo"
    assert body["journal_entry"]["ledger"] == "memo"
    assert len(client.get("/api/purchase-orders", params={"status": "open"}).json()) == open_before + 1

    received = client.post(f"/api/purchase-orders/{po['id']}/receive")
    assert received.status_code == 200 and received.json()["status"] == "received"
    entries = client.get("/api/ledger/entries").json()
    receipt = next(e for e in entries if e["source_type"] == "po_receipt")
    release = next(e for e in entries if e["source_type"] == "po_commitment_release")
    assert _balances(receipt) == (4000.0, 4000.0)
    assert {l["account_code"] for l in receipt["lines"]} == {"1300", "2100"}
    assert _balances(release) == (4000.0, 4000.0)

    after = next(s for s in client.get("/api/inventory/skus").json() if s["id"] == sku["id"])
    assert after["on_hand"] == sku["on_hand"] + 100


def test_reject_keeps_ledger_untouched(client):
    sku = _sku(client, "Accessories")
    d = client.post("/api/decisions", json={"kind": "write_off", "title": "x", "payload": {"sku_id": sku["id"], "quantity": 1}}).json()
    r = client.post(f"/api/decisions/{d['id']}/reject", json={"rejected_by": "Olivia Hayes", "note": "Bundle instead"})
    assert r.json()["status"] == "rejected"
    assert client.get("/api/ledger/entries").json() == []


def test_unconfigured_connectors_cannot_connect_or_become_abor(client):
    by_id = {c["id"]: c for c in client.get("/api/connectors").json()}
    assert by_id["demo"]["connected"] and by_id["demo"]["is_abor"]
    assert "journal_entries" not in by_id["salesforce"]["capabilities"]
    assert "purchase_orders" in by_id["salesforce"]["capabilities"]

    r = client.post("/api/connectors/sap_s4hana/connect")
    assert r.status_code == 409 and "PROSIGHT_SAP_BASE_URL" in r.json()["detail"]
    assert client.put("/api/settings/routing", json={"abor_connector": "salesforce"}).status_code == 409
    assert client.post("/api/connectors/demo/disconnect").status_code == 409


def test_live_connector_failure_leaves_decision_pending(client, monkeypatch):
    from prosight.config import settings

    monkeypatch.setattr(settings, "salesforce_instance_url", "https://example.my.salesforce.com")
    monkeypatch.setattr(settings, "salesforce_access_token", "token")
    assert client.post("/api/connectors/salesforce/connect").status_code == 200
    assert client.put("/api/settings/routing", json={"po_connector": "salesforce"}).status_code == 200

    sku, vendor = _sku(client, "Basics"), _vendor(client)
    d = client.post("/api/decisions", json={"kind": "purchase_order", "title": "PO", "payload": {
        "sku_id": sku["id"], "vendor_id": vendor["id"], "quantity": 10, "unit_cost": 5.0}}).json()
    r = client.post(f"/api/decisions/{d['id']}/approve", json={"approved_by": "Olivia Hayes"})
    assert r.status_code == 502
    after = next(x for x in client.get("/api/decisions").json() if x["id"] == d["id"])
    assert after["status"] == "pending" and "Salesforce" in after["last_error"]
    assert client.get("/api/ledger/entries").json() == []

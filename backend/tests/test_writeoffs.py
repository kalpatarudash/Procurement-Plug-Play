import pytest

from prosight.writeoffs import ASSUMPTIONS, _sell


def test_sell_respects_fifo_and_deadlines():
    demand = [10.0] * 10
    # Oldest lot is past its deadline: nothing sells; the next lot gets all the demand.
    sold, clear_week, leftover = _sell([(50, 0), (30, 10)], demand)
    assert sold == 30 and clear_week is None and leftover == pytest.approx(70)
    # Both lots sell in time, oldest first.
    sold, clear_week, leftover = _sell([(20, 5), (30, 10)], demand)
    assert sold == 50 and clear_week == 5 and leftover == pytest.approx(50)
    # Deadline cuts the lot short.
    sold, _, _ = _sell([(100, 3)], demand)
    assert sold == 30


def _run(client):
    return client.get("/api/writeoffs").json()


def test_plans_are_internally_consistent(client):
    run = _run(client)
    assert run["plans"], "seed data has aged accessories"
    min_value = ASSUMPTIONS["min_value_usd"]
    for p in run["plans"]:
        kinds = [a["kind"] for a in p["actions"]]
        assert p["aged_units"] == p["hold"]["aged_sold"] + p["hold"]["write_off_units"]
        assert p["cash_gain_vs_hold"] == pytest.approx(p["markdown"]["cash"] - p["hold"]["cash"], abs=0.02)
        assert all(a["value"] >= min_value for a in p["actions"])
        if p["recommendation"] == "markdown":
            assert kinds[0] == "markdown" and p["cash_gain_vs_hold"] >= min_value
        elif p["recommendation"] == "write_off":
            assert kinds == ["write_off"] and p["actions"][0]["quantity"] == p["hold"]["write_off_units"]
        else:
            assert kinds == []


def test_obsolete_stock_is_fully_written_off(client):
    obsolete = [p for p in _run(client)["plans"] if p["oldest_days"] > ASSUMPTIONS["max_age_days"] and p["hold"]["aged_sold"] == 0]
    assert obsolete
    for p in obsolete:
        assert p["recommendation"] == "write_off" and p["actions"][0]["quantity"] == p["aged_units"]


def test_refresh_is_idempotent(client):
    first = client.post("/api/writeoffs/refresh").json()
    expected = sum(len(p["actions"]) for p in first["run"]["plans"])
    assert first["created"] == expected > 0
    client.post("/api/writeoffs/refresh")
    pending = [d for d in client.get("/api/decisions", params={"status": "pending"}).json() if d["kind"] in ("write_off", "markdown")]
    assert len(pending) == expected


def test_approving_model_write_off_posts_and_removes_aged_stock(client):
    client.post("/api/writeoffs/refresh")
    d = next(d for d in client.get("/api/decisions", params={"status": "pending"}).json() if d["kind"] == "write_off")
    sku_before = next(s for s in client.get("/api/inventory/skus").json() if s["id"] == d["payload"]["sku_id"])
    body = client.post(f"/api/decisions/{d['id']}/approve", json={"approved_by": "Olivia Hayes"}).json()
    assert body["status"] == "approved"
    lines = {l["account_code"]: l for l in body["journal_entry"]["lines"]}
    assert lines["5150"]["debit"] == lines["1300"]["credit"] > 0
    sku_after = next(s for s in client.get("/api/inventory/skus").json() if s["id"] == d["payload"]["sku_id"])
    assert sku_after["on_hand"] == sku_before["on_hand"] - d["payload"]["quantity"]
    assert sku_after["aged_units"] == sku_before["aged_units"] - d["payload"]["quantity"]


def test_edited_write_off_quantity_updates_title_and_amount(client):
    client.post("/api/writeoffs/refresh")
    d = next(d for d in client.get("/api/decisions", params={"status": "pending"}).json()
             if d["kind"] == "write_off" and d["payload"]["quantity"] > 10)
    qty = d["payload"]["quantity"] - 10
    body = client.post(f"/api/decisions/{d['id']}/approve", json={"approved_by": "Olivia Hayes", "quantity": qty}).json()
    assert body["title"].startswith(f"Write off {qty:,} aged")
    assert body["payload"]["quantity"] == qty


def _markdown(client, sku, quantity, price):
    return client.post("/api/decisions", json={
        "kind": "markdown", "title": "Clearance",
        "payload": {"sku_id": sku["id"], "quantity": quantity, "clearance_price": price},
    }).json()


def test_markdown_below_cost_writes_down_to_nrv(client):
    sku = next(s for s in client.get("/api/inventory/skus").json() if s["aged_units"] >= 10)
    value_before = client.get("/api/inventory").json()["total_value"]
    price = round(sku["unit_cost"] * 0.5, 2)
    d = _markdown(client, sku, 10, price)
    body = client.post(f"/api/decisions/{d['id']}/approve", json={"approved_by": "Olivia Hayes"}).json()
    nrv = round(price * (1 - ASSUMPTIONS["selling_cost_rate"]), 2)
    expected = round(10 * (sku["unit_cost"] - nrv), 2)
    lines = {l["account_code"]: l for l in body["journal_entry"]["lines"]}
    assert lines["5160"]["debit"] == pytest.approx(expected, abs=0.02)
    assert lines["1300"]["credit"] == pytest.approx(expected, abs=0.02)
    value_after = client.get("/api/inventory").json()["total_value"]
    assert value_before - value_after == pytest.approx(expected, abs=0.05)
    sku_after = next(s for s in client.get("/api/inventory/skus").json() if s["id"] == sku["id"])
    assert sku_after["on_hand"] == sku["on_hand"]  # markdown keeps the stock, only revalues it


def test_markdown_above_cost_posts_nothing(client):
    sku = next(s for s in client.get("/api/inventory/skus").json() if s["aged_units"] >= 10)
    d = _markdown(client, sku, 10, round(sku["unit_cost"] * 2, 2))
    body = client.post(f"/api/decisions/{d['id']}/approve", json={"approved_by": "Olivia Hayes"}).json()
    assert body["status"] == "approved" and body["journal_entry"] is None
    assert client.get("/api/ledger/entries").json() == []

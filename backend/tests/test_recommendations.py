import pytest

from prosight.recommendations import BASE_WEIGHTS, scoring_weights


def _run(client):
    return client.get("/api/recommendations").json()


def test_forecast_beats_naive_baseline(client):
    fc = _run(client)["forecast"]
    assert fc["backtest_wape"] < fc["naive_wape"]
    assert abs(fc["backtest_bias"]) < 0.05


def test_emission_pressure_shifts_weight_to_emissions():
    calm, pressured = scoring_weights(0.0), scoring_weights(0.6)
    assert calm == pytest.approx(BASE_WEIGHTS)
    assert sum(pressured.values()) == pytest.approx(1.0)
    assert pressured["emissions"] > calm["emissions"]
    assert pressured["cost"] < calm["cost"]


def test_every_sku_gets_a_plan_with_consistent_fields(client):
    run = _run(client)
    skus = client.get("/api/inventory/skus").json()
    assert len(run["plans"]) == len(skus)
    for p in run["plans"]:
        assert p["status"] in {"order_now", "upcoming", "healthy", "overstocked"}
        assert len(p["weekly_forecast"]) == run["forecast"]["horizon_weeks"]
        if p["status"] == "order_now":
            assert p["quantity"] > 0 and p["vendor"] and p["order_by"] == run["as_of"]
            assert p["amount"] == pytest.approx(p["quantity"] * p["vendor"]["unit_cost"], abs=0.01)
        if p["vendor"] and p["alternatives"]:
            assert p["vendor"]["score"] <= min(a["score"] for a in p["alternatives"])


def test_refresh_creates_decisions_and_is_idempotent(client):
    first = client.post("/api/recommendations/refresh").json()
    order_now = [p for p in first["run"]["plans"] if p["status"] == "order_now"]
    assert first["created"] == len(order_now) >= 1
    client.post("/api/recommendations/refresh")
    pending = client.get("/api/decisions", params={"status": "pending"}).json()
    assert len(pending) == len(order_now)
    assert all(d["source"] == "model" and d["details"]["vendor_options"] for d in pending)


def test_approve_with_edits_reprices_and_posts(client):
    client.post("/api/recommendations/refresh")
    decision = client.get("/api/decisions", params={"status": "pending"}).json()[0]
    options = decision["details"]["vendor_options"]
    if len(options) < 2:
        pytest.skip("SKU has a single supplier")
    alt = options[1]
    body = client.post(f"/api/decisions/{decision['id']}/approve",
                       json={"approved_by": "Olivia Hayes", "quantity": 120, "vendor_id": alt["vendor_id"]}).json()
    po = body["purchase_order"]
    assert body["status"] == "approved"
    assert po["quantity"] == 120 and po["vendor"] == alt["name"]
    assert po["unit_cost"] == pytest.approx(alt["unit_cost"], abs=0.01)
    assert body["journal_entry"]["ledger"] == "memo"
    assert alt["name"] in body["title"]


def test_raise_upcoming_early_survives_refresh(client):
    upcoming = next(p for p in _run(client)["plans"] if p["status"] == "upcoming" and p["quantity"] > 0)
    r = client.post(f"/api/recommendations/{upcoming['sku_id']}/raise")
    assert r.status_code == 201
    assert client.post(f"/api/recommendations/{upcoming['sku_id']}/raise").json()["id"] == r.json()["id"]
    client.post("/api/recommendations/refresh")
    pending_ids = {d["id"] for d in client.get("/api/decisions", params={"status": "pending"}).json()}
    assert r.json()["id"] in pending_ids


def test_rejected_recommendations_are_not_re_raised(client):
    client.post("/api/recommendations/refresh")
    client.post("/api/writeoffs/refresh")
    pending = client.get("/api/decisions", params={"status": "pending"}).json()
    po = next(d for d in pending if d["kind"] == "purchase_order")
    wo = next(d for d in pending if d["kind"] == "write_off")
    for d in (po, wo):
        assert client.post(f"/api/decisions/{d['id']}/reject", json={"rejected_by": "Olivia Hayes"}).status_code == 200
    client.post("/api/recommendations/refresh")
    client.post("/api/writeoffs/refresh")
    again = client.get("/api/decisions", params={"status": "pending"}).json()
    assert not any(d["kind"] == "purchase_order" and d["payload"]["sku_id"] == po["payload"]["sku_id"] for d in again)
    assert not any(d["kind"] in ("write_off", "markdown") and d["payload"]["sku_id"] == wo["payload"]["sku_id"] for d in again)


def test_raise_rejected_when_nothing_needed(client):
    covered = next(p for p in _run(client)["plans"] if p["status"] == "overstocked")
    assert client.post(f"/api/recommendations/{covered['sku_id']}/raise").status_code == 409


def test_sku_forecast_endpoint(client):
    sku = client.get("/api/inventory/skus").json()[0]
    body = client.get(f"/api/forecast/{sku['id']}").json()
    assert len(body["history"]) == len(body["history_weeks"]) == 26
    assert len(body["forecast"]) == len(body["forecast_weeks"])
    assert body["forecast_weeks"][0] > body["history_weeks"][-1]
    assert client.get("/api/forecast/99999").status_code == 404


def test_approve_override_validation(client):
    client.post("/api/recommendations/refresh")
    decision = client.get("/api/decisions", params={"status": "pending"}).json()[0]
    bad = client.post(f"/api/decisions/{decision['id']}/approve", json={"approved_by": "x", "vendor_id": 99999})
    assert bad.status_code == 409
    still = next(d for d in client.get("/api/decisions").json() if d["id"] == decision["id"])
    assert still["status"] == "pending" and still["payload"] == decision["payload"]

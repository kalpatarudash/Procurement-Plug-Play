import pytest

from prosight import emissions


def test_epa_factor_set_loads_real_values():
    # Spot checks against the EPA v1.3 CSV (kg CO2e per 2022 USD, with margins).
    assert emissions.industry_factor("316210").kg_co2e_per_usd == pytest.approx(0.282)
    assert emissions.industry_factor("314120").title == "Curtain and Linen Mills"
    assert len(emissions.epa_factors()) > 1000
    with pytest.raises(ValueError):
        emissions.industry_factor("999999")


def test_scope_1_2_factors_follow_epa_hub():
    assert emissions.ELECTRICITY_KG_PER_KWH == pytest.approx(0.3516, abs=1e-4)
    assert emissions.NATURAL_GAS_KG_PER_THERM == pytest.approx(5.3115, abs=1e-4)


def test_vendor_factor_applies_intensity_index(client):
    vendors = {v["name"]: v for v in client.get("/api/vendors").json()}
    evergreen = vendors["Evergreen Manufacturing"]
    assert evergreen["kg_co2e_per_usd"] == pytest.approx(evergreen["industry_kg_co2e_per_usd"] * 0.82, abs=1e-4)
    assert client.get("/api/emission-factors/316210").json()["title"] == "Footwear Manufacturing"
    assert client.get("/api/emission-factors/000000").status_code == 404


def test_emissions_endpoint_totals_scopes(client):
    body = client.get("/api/emissions", params={"view": "YTD"}).json()
    cur = body["current"]
    assert cur["scope1"] > 0 and cur["scope2"] > 0 and cur["scope3"] > 0
    assert cur["total"] == pytest.approx(cur["scope1"] + cur["scope2"] + cur["scope3"], abs=0.2)
    assert sum(body["scope3_by_vendor"].values()) == pytest.approx(cur["scope3"], abs=0.5)


def test_plan_preview_math(client):
    a = client.get("/api/plan", params={"revenue_target": 6_000_000, "emission_reduction_pct": 20}).json()["analysis"]
    rev, margins, proc = a["revenue"], a["margins"], a["procurement"]
    assert rev["required_growth"] == pytest.approx(6_000_000 / rev["baseline"] - 1)
    assert margins["target_cogs"] == pytest.approx(6_000_000 * (1 - margins["gross_margin"]), abs=1)
    assert proc["purchase_budget"] == pytest.approx(
        margins["target_cogs"] + proc["target_ending_inventory"] - proc["current_inventory"], abs=1)
    assert sum(m["revenue_target"] for m in a["monthly"]) == pytest.approx(6_000_000, rel=1e-6)

    em = a["emissions"]
    assert em["target_total"] == pytest.approx(em["baseline"]["total"] * 0.8, abs=0.2)
    assert em["gap"] == pytest.approx(max(0, em["projected"]["total"] - em["target_total"]), abs=0.2)
    costs = [l["cost_per_t"] for l in em["levers"]]
    assert costs == sorted(costs)
    assert em["on_track"] == (em["selected_reduction"] >= em["gap"])


def test_higher_reduction_target_needs_more_levers(client):
    low = client.get("/api/plan", params={"emission_reduction_pct": 5}).json()["analysis"]["emissions"]
    high = client.get("/api/plan", params={"emission_reduction_pct": 40}).json()["analysis"]["emissions"]
    assert high["gap"] > low["gap"]
    assert sum(l["selected"] for l in high["levers"]) >= sum(l["selected"] for l in low["levers"])
    assert high["emission_pressure"] > low["emission_pressure"]


def test_save_plan_persists_targets(client):
    assert client.get("/api/plan").json()["saved"] is None
    r = client.put("/api/plan", json={"revenue_target": 5_600_000, "emission_reduction_pct": 18, "updated_by": "Olivia Hayes"})
    assert r.status_code == 200
    body = client.get("/api/plan").json()
    assert body["saved"]["revenue_target"] == 5_600_000
    assert body["analysis"]["revenue"]["target"] == 5_600_000
    assert body["analysis"]["emissions"]["reduction_pct"] == 18


def test_plan_validation(client):
    assert client.put("/api/plan", json={"revenue_target": 0, "emission_reduction_pct": 10, "updated_by": "x"}).status_code == 422
    assert client.put("/api/plan", json={"revenue_target": 5e6, "emission_reduction_pct": 95, "updated_by": "x"}).status_code == 422
    assert client.get("/api/plan", params={"revenue_target": 1e9}).status_code == 422

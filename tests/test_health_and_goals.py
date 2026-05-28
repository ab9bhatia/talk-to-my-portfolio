from fastapi.testclient import TestClient

from main import app


def test_health_endpoint():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "ok"


def test_goals_roundtrip():
    client = TestClient(app)
    get_res = client.get("/api/portfolio/profile/goals")
    assert get_res.status_code == 200
    current = get_res.json()

    payload = {
        "target_return_pct": 16.5,
        "max_position_pct": 11.0,
        "max_sector_pct": 29.0,
        "cash_buffer_pct": 6.0,
        "risk_profile": "moderate",
    }
    put_res = client.put("/api/portfolio/profile/goals", json=payload)
    assert put_res.status_code == 200
    assert put_res.json()["ok"] is True

    after = client.get("/api/portfolio/profile/goals").json()
    assert float(after["target_return_pct"]) == payload["target_return_pct"]
    assert float(after["max_position_pct"]) == payload["max_position_pct"]
    assert after["risk_profile"] == payload["risk_profile"]

    # restore prior values so test is non-destructive for local usage
    client.put(
        "/api/portfolio/profile/goals",
        json={
            "target_return_pct": float(current["target_return_pct"]),
            "max_position_pct": float(current["max_position_pct"]),
            "max_sector_pct": float(current["max_sector_pct"]),
            "cash_buffer_pct": float(current["cash_buffer_pct"]),
            "risk_profile": current["risk_profile"],
        },
    )

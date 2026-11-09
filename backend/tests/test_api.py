from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_paper_only():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"
    assert response.json()["live_trading_enabled"] is False


def test_order_rejects_non_eur_pair_before_market_request():
    response = client.post(
        "/api/orders",
        json={"symbol": "BTC/USDT", "side": "buy", "quantity": 0.001},
    )
    assert response.status_code == 422

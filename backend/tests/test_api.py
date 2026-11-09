from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.portfolio import PaperPortfolio

client = TestClient(app)


def test_health_is_paper_only():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"
    assert response.json()["live_trading_enabled"] is False


def test_order_rejects_non_eur_pair_before_market_request(monkeypatch):
    def unexpected_ticker(_symbol):
        raise AssertionError("Market data must not be requested for unsupported quote currencies")

    monkeypatch.setattr("app.main.market.ticker", unexpected_ticker)
    response = client.post(
        "/api/orders",
        json={"symbol": "BTC/USDT", "side": "buy", "quantity": 0.001},
    )
    assert response.status_code == 422


def test_order_rejects_stale_market_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.main.portfolio",
        PaperPortfolio(str(tmp_path / "paper.db"), initial_cash=20),
    )
    stale_timestamp = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(
        "app.main.market.ticker",
        lambda symbol: {"symbol": symbol, "last": 10000, "timestamp": stale_timestamp},
    )
    response = client.post(
        "/api/orders",
        json={"symbol": "BTC/EUR", "side": "buy", "quantity": 0.0001},
    )
    assert response.status_code == 503
    assert "stale" in response.json()["detail"].lower()

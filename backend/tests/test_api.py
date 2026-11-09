from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.portfolio import PaperPortfolio

client = TestClient(app)


def configure_order_auth(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "test-control-token")
    return {"X-Agent-Token": "test-control-token"}


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


def test_order_api_requires_control_token(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "test-control-token")
    response = client.post(
        "/api/orders",
        json={
            "symbol": "BTC/EUR",
            "side": "buy",
            "quantity": 0.0004,
            "stop_loss": 9800,
        },
    )
    assert response.status_code == 401


def test_order_api_is_disabled_without_control_token(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "")
    response = client.post(
        "/api/orders",
        json={
            "symbol": "BTC/EUR",
            "side": "buy",
            "quantity": 0.0004,
            "stop_loss": 9800,
        },
        headers={"X-Agent-Token": "anything"},
    )
    assert response.status_code == 503


def test_buy_order_requires_stop_loss_before_market_request(monkeypatch):
    def unexpected_ticker(_symbol, provider_id=None):
        raise AssertionError("A buy without stop-loss must be rejected before data access")

    monkeypatch.setattr("app.main.market.ticker", unexpected_ticker)
    response = client.post(
        "/api/orders",
        json={"symbol": "BTC/EUR", "side": "buy", "quantity": 0.0004},
        headers=configure_order_auth(monkeypatch),
    )
    assert response.status_code == 422
    assert "stop_loss" in response.json()["detail"]


def test_order_rejects_stale_market_data(monkeypatch, tmp_path):
    headers = configure_order_auth(monkeypatch)
    monkeypatch.setattr(
        "app.main.portfolio",
        PaperPortfolio(str(tmp_path / "paper.db"), initial_cash=20),
    )
    stale_timestamp = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(
        "app.main.market.ticker",
        lambda symbol, provider_id=None: {
            "symbol": symbol, "last": 10000, "timestamp": stale_timestamp,
        },
    )
    response = client.post(
        "/api/orders",
        json={
            "symbol": "BTC/EUR",
            "side": "buy",
            "quantity": 0.0001,
            "stop_loss": 9800,
        },
        headers=headers,
    )
    assert response.status_code == 503
    assert "stale" in response.json()["detail"].lower()


def test_manual_buy_passes_risk_manager_and_persists_protection(monkeypatch, tmp_path):
    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "paper.db"), initial_cash=20)
    monkeypatch.setattr("app.main.portfolio", paper)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(
        "app.main.market.ticker",
        lambda symbol, provider_id=None: {
            "symbol": symbol, "last": 10000, "timestamp": timestamp,
        },
    )

    response = client.post(
        "/api/orders",
        json={
            "symbol": "BTC/EUR",
            "side": "buy",
            "quantity": 0.0004,
            "stop_loss": 9800,
            "take_profit": 10500,
            "strategy_id": "manual_test",
        },
        headers=headers,
    )

    assert response.status_code == 201
    position = paper.snapshot()["positions"][0]
    assert position["stop_loss"] == 9800
    assert position["take_profit"] == 10500
    assert position["strategy_id"] == "manual_test"


def test_manual_buy_is_rejected_when_stop_distance_exceeds_risk_limit(monkeypatch, tmp_path):
    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "paper.db"), initial_cash=20)
    monkeypatch.setattr("app.main.portfolio", paper)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(
        "app.main.market.ticker",
        lambda symbol, provider_id=None: {
            "symbol": symbol, "last": 10000, "timestamp": timestamp,
        },
    )
    response = client.post(
        "/api/orders",
        json={
            "symbol": "BTC/EUR",
            "side": "buy",
            "quantity": 0.0004,
            "stop_loss": 9000,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "Risk Manager rejected" in response.json()["detail"]
    assert paper.snapshot()["positions"] == []


def test_order_rejects_non_positive_quantity_before_market_request(monkeypatch):
    def unexpected_ticker(_symbol):
        raise AssertionError("Invalid quantity must be rejected before market data access")

    monkeypatch.setattr("app.main.market.ticker", unexpected_ticker)
    response = client.post(
        "/api/orders",
        json={"symbol": "BTC/EUR", "side": "buy", "quantity": 0},
    )
    assert response.status_code == 422


def test_equity_research_endpoint_is_read_only_and_requires_configured_provider(monkeypatch):
    from app.providers.registry import ProviderRegistryError

    def unavailable(_provider_id):
        raise ProviderRegistryError("Provider is not configured: twelve_data")

    monkeypatch.setattr("app.main.market.get", unavailable)
    response = client.get("/api/research/equities")
    assert response.status_code == 503
    assert "twelve_data" in response.json()["detail"]


def test_equity_research_endpoint_validates_scan_bounds():
    response = client.get("/api/research/equities?max_instruments=100")
    assert response.status_code == 422


def test_agent_task_request_accepts_read_only_equity_research_kind():
    from app.agent_api import TaskRequest

    task = TaskRequest(kind="equity_research", payload={"max_instruments": 3})
    assert task.kind == "equity_research"
    assert task.payload["max_instruments"] == 3
    backup_task = TaskRequest(kind="database_backup")
    assert backup_task.kind == "database_backup"



def test_readiness_requires_database_and_recent_worker_heartbeat(monkeypatch):
    from datetime import timedelta

    fresh = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr("app.main.agent_store.state", lambda: {"last_heartbeat": fresh})

    response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database"] == "ok"
    assert response.json()["worker"] == "ok"
    assert response.json()["worker_heartbeat_age_seconds"] < 30


def test_readiness_rejects_stale_worker_heartbeat(monkeypatch):
    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    monkeypatch.setattr("app.main.agent_store.state", lambda: {"last_heartbeat": stale})

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert "heartbeat is stale" in response.json()["detail"]


def test_readiness_rejects_database_failure(monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.main.agent_store.connect", unavailable)
    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert "readiness check failed" in response.json()["detail"]

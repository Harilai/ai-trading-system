from datetime import datetime, timedelta, timezone

import pytest

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



def test_strategy_history_endpoint_requires_agent_token(monkeypatch):
    monkeypatch.setattr("app.agent_api.settings.agent_control_token", "test-control-token")
    response = client.get("/api/agent/strategy-history")
    assert response.status_code == 401


def test_strategy_history_endpoint_returns_filtered_persisted_evidence(monkeypatch):
    monkeypatch.setattr("app.agent_api.settings.agent_control_token", "test-control-token")
    expected = [{
        "task_id": "scan-1",
        "strategy_id": "ema_12_26",
        "eligible": False,
        "validation_failures": ["does_not_beat_buy_and_hold"],
    }]
    calls = {}

    def fake_history(**kwargs):
        calls.update(kwargs)
        return expected

    monkeypatch.setattr("app.agent_api.store.strategy_history", fake_history)
    response = client.get(
        "/api/agent/strategy-history?limit=25&strategy_id=ema_12_26&instrument_id=ccxt%3Akraken%3Aspot%3ABTC%2FEUR",
        headers={"X-Agent-Token": "test-control-token"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == expected
    assert calls == {
        "limit": 25,
        "strategy_id": "ema_12_26",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
    }


def test_strategy_history_endpoint_validates_limit(monkeypatch):
    monkeypatch.setattr("app.agent_api.settings.agent_control_token", "test-control-token")
    response = client.get(
        "/api/agent/strategy-history?limit=0",
        headers={"X-Agent-Token": "test-control-token"},
    )
    assert response.status_code == 422



def test_dashboard_route_serves_operations_ui():
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AI Trading System" in response.text
    assert "PAPER TRADING" in response.text
    assert "localStorage" not in response.text
    assert "Mercado · velas OHLCV" in response.text
    assert "Proveedores y readiness" in response.text
    assert "Órdenes paper y conciliación" in response.text
    assert "Backups y restauración" in response.text
    assert "Retiradas de cartera paper" in response.text
    assert "No se realizará ninguna transferencia real" in response.text


def test_fx_rate_endpoint_returns_fresh_validated_rate(monkeypatch):
    from datetime import datetime, timezone

    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeFxProvider:
        def exchange_rate(self, symbol):
            assert symbol == "EUR/USD"
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeFxProvider())
    response = client.get("/api/fx/EUR/USD")
    assert response.status_code == 200
    assert response.json()["rate"] == 1.1
    assert response.json()["provider_id"] == "twelve_data"


def test_fx_rate_endpoint_inverts_the_same_validated_observation(monkeypatch):
    from datetime import datetime, timezone

    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeFxProvider:
        calls = 0

        def exchange_rate(self, symbol):
            self.calls += 1
            return {
                "symbol": "EUR/USD", "rate": 1.25,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    provider = FakeFxProvider()
    monkeypatch.setattr("app.main.market.get", lambda provider_id: provider)
    response = client.get("/api/fx/USD/EUR")
    assert response.status_code == 200
    assert response.json()["rate"] == 0.8
    assert response.json()["inverted"] is True
    assert provider.calls == 1


def test_fx_conversion_api_requires_owner_token(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "test-control-token")
    response = client.post(
        "/api/agent/fx-conversions",
        json={
            "from_currency": "EUR", "to_currency": "USD", "amount": 5,
            "client_conversion_id": "conversion-auth",
        },
    )
    assert response.status_code == 401


def test_fx_conversion_api_persists_and_replays_without_second_fx_lookup(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "fx-api.db"), initial_cash=20)
    monkeypatch.setattr("app.main.portfolio", paper)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeFxProvider:
        calls = 0

        def exchange_rate(self, symbol):
            self.calls += 1
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    provider = FakeFxProvider()
    monkeypatch.setattr("app.main.market.get", lambda provider_id: provider)
    payload = {
        "from_currency": "EUR", "to_currency": "USD", "amount": 5,
        "client_conversion_id": "conversion-api-1",
    }
    first = client.post("/api/agent/fx-conversions", json=payload, headers=headers)
    replay = client.post("/api/agent/fx-conversions", json=payload, headers=headers)
    conflict = client.post(
        "/api/agent/fx-conversions",
        json={**payload, "amount": 19},
        headers=headers,
    )
    assert first.status_code == 201
    assert replay.status_code == 201
    assert conflict.status_code == 409
    assert first.json()["net_to_amount"] == pytest.approx(5.4945)
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["idempotent_replay"] is True
    assert provider.calls == 1
    assert {row["currency"]: row["balance"] for row in paper.cash_balances()} == {
        "EUR": pytest.approx(15),
        "USD": pytest.approx(5.4945),
    }


def test_fx_conversion_api_rejects_stale_fx_observation(monkeypatch, tmp_path):
    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "fx-api.db"), initial_cash=20)
    monkeypatch.setattr("app.main.portfolio", paper)

    class FakeFxProvider:
        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": 1_600_000_000_000, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeFxProvider())
    response = client.post(
        "/api/agent/fx-conversions",
        json={
            "from_currency": "EUR", "to_currency": "USD", "amount": 5,
            "client_conversion_id": "stale-conversion",
        },
        headers=headers,
    )
    assert response.status_code == 503
    assert "stale" in response.json()["detail"].lower()
    assert {row["currency"]: row["balance"] for row in paper.cash_balances()} == {
        "EUR": pytest.approx(20),
    }


def test_manual_usd_equity_paper_order_requires_conversion_and_records_native_currency(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "equity-paper.db"), initial_cash=1000)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 100, 1.1, 0, "pre-fund-usd", "twelve_data", timestamp,
    )

    class FakeEquityProvider:
        def discover_markets(self, asset_classes=None):
            return [MarketInstrument(
                provider_id="twelve_data",
                symbol="AAPL",
                asset_class=AssetClass.EQUITY,
                market_type=MarketType.CASH_EQUITY,
                base="AAPL",
                quote="USD",
                active=True,
                metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
            )]

        def ticker(self, symbol):
            return {
                "symbol": symbol, "last": 100, "bid": 99.9, "ask": 100.1,
                "timestamp": timestamp, "currency": "USD", "is_market_open": True,
            }

        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeEquityProvider())
    payload = {
        "symbol": "AAPL", "provider_id": "twelve_data",
        "side": "buy", "quantity": 0.5, "stop_loss": 95.2,
        "client_order_id": "manual-aapl-1",
    }
    response = client.post("/api/orders", json=payload, headers=headers)
    replay = client.post("/api/orders", json=payload, headers=headers)
    conflict = client.post(
        "/api/orders",
        json={**payload, "quantity": 0.25},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["id"] == response.json()["id"]
    assert conflict.status_code == 409
    trade = response.json()
    assert trade["quote_currency"] == "USD"
    assert trade["provider_id"] == "twelve_data"
    assert trade["fx_rate_to_eur"] == pytest.approx(1 / 1.1)
    position = paper.snapshot()["positions"][0]
    assert position["symbol"] == "AAPL"
    assert position["quote_currency"] == "USD"
    assert position["quantity"] == pytest.approx(0.5)


def test_manual_usd_equity_order_cannot_spend_eur_without_conversion(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "equity-paper.db"), initial_cash=1000)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeEquityProvider:
        def discover_markets(self, asset_classes=None):
            return [MarketInstrument(
                provider_id="twelve_data", symbol="AAPL",
                asset_class=AssetClass.EQUITY, market_type=MarketType.CASH_EQUITY,
                base="AAPL", quote="USD", active=True,
                metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
            )]

        def ticker(self, symbol):
            return {
                "symbol": symbol, "last": 100, "bid": 99.9, "ask": 100.1,
                "timestamp": timestamp, "currency": "USD", "is_market_open": True,
            }

        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeEquityProvider())
    response = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL", "provider_id": "twelve_data",
            "side": "buy", "quantity": 1, "stop_loss": 95.2,
            "client_order_id": "manual-aapl-no-usd",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "Insufficient available USD" in response.json()["detail"]
    assert paper.snapshot()["positions"] == []


def test_manual_equity_order_rejects_closed_or_unknown_exchange_session(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "equity-paper.db"), initial_cash=1000)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 100, 1.1, 0, "pre-fund-usd", "twelve_data", timestamp,
    )

    class FakeEquityProvider:
        def discover_markets(self, asset_classes=None):
            return [MarketInstrument(
                provider_id="twelve_data", symbol="AAPL",
                asset_class=AssetClass.EQUITY, market_type=MarketType.CASH_EQUITY,
                base="AAPL", quote="USD", active=True,
                metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
            )]

        def ticker(self, symbol):
            return {
                "symbol": symbol, "last": 100, "bid": 99.9, "ask": 100.1,
                "timestamp": timestamp, "currency": "USD", "is_market_open": False,
            }

        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeEquityProvider())
    response = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL", "provider_id": "twelve_data",
            "side": "buy", "quantity": 0.5, "stop_loss": 95.2,
            "client_order_id": "manual-aapl-closed",
        },
        headers=headers,
    )
    assert response.status_code == 503
    assert "session is closed or unknown" in response.json()["detail"]
    assert paper.snapshot()["positions"] == []


def test_cash_equity_risk_profile_is_manual_paper_only():
    response = client.get("/api/risk-profiles")
    assert response.status_code == 200
    profile = response.json()["cash_equity"]
    assert profile["enabled"] is True
    assert profile["manual_paper_only"] is True
    assert profile["autonomous_enabled"] is False
    assert profile["shorting"] is False
    assert "explicit_fx_conversion" in profile["controls"]


def test_manual_equity_paper_order_uses_last_price_when_bid_ask_unavailable(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "equity-last-price.db"), initial_cash=1000)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 100, 1.1, 0, "prefund-last-price", "twelve_data", timestamp,
    )

    class FakeEquityProvider:
        def discover_markets(self, asset_classes=None):
            return [MarketInstrument(
                provider_id="twelve_data", symbol="MSFT",
                asset_class=AssetClass.EQUITY, market_type=MarketType.CASH_EQUITY,
                base="MSFT", quote="USD", active=True,
                metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
            )]

        def ticker(self, symbol):
            return {
                "symbol": symbol, "last": 100, "bid": None, "ask": None,
                "timestamp": timestamp, "currency": "USD", "is_market_open": True,
            }

        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeEquityProvider())
    response = client.post(
        "/api/orders",
        json={
            "symbol": "MSFT", "provider_id": "twelve_data",
            "side": "buy", "quantity": 0.5, "stop_loss": 95.1,
            "client_order_id": "manual-msft-last-price",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["price"] == pytest.approx(100.1)
    assert response.json()["quote_currency"] == "USD"


def test_manual_equity_order_rejects_excessive_bid_ask_spread(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "equity-wide-spread.db"), initial_cash=1000)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 100, 1.1, 0, "prefund-wide-spread", "twelve_data", timestamp,
    )

    class FakeEquityProvider:
        def discover_markets(self, asset_classes=None):
            return [MarketInstrument(
                provider_id="twelve_data", symbol="MSFT",
                asset_class=AssetClass.EQUITY, market_type=MarketType.CASH_EQUITY,
                base="MSFT", quote="USD", active=True,
                metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
            )]

        def ticker(self, symbol):
            return {
                "symbol": symbol, "last": 100, "bid": 90, "ask": 110,
                "timestamp": timestamp, "currency": "USD", "is_market_open": True,
            }

        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeEquityProvider())
    response = client.post(
        "/api/orders",
        json={
            "symbol": "MSFT", "provider_id": "twelve_data",
            "side": "buy", "quantity": 0.5, "stop_loss": 95.1,
            "client_order_id": "manual-msft-wide-spread",
        },
        headers=headers,
    )
    assert response.status_code == 503
    assert "spread" in response.json()["detail"].lower()
    assert paper.snapshot()["positions"] == []


def test_portfolio_endpoint_consolidates_usd_cash_with_fx_provenance(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    paper = PaperPortfolio(str(tmp_path / "portfolio-fx.db"), initial_cash=100)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 50, 1.1, 0, "portfolio-fx", "twelve_data", timestamp,
    )

    class FakeFxProvider:
        def exchange_rate(self, symbol):
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeFxProvider())
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["valuation_complete"] is True
    assert payload["cash_total_eur"] == pytest.approx(100)
    assert payload["fx_observations"]["USD"]["provider_id"] == "twelve_data"
    assert payload["fx_observations"]["USD"]["timestamp"] == timestamp
    usd = next(item for item in payload["cash_balances"] if item["currency"] == "USD")
    assert usd["fx_rate_to_eur"] == pytest.approx(1 / 1.1)
    assert usd["value_eur"] == pytest.approx(50)


def test_fx_conversion_api_supports_usd_to_eur_using_inverse_rate(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "fx-api-reverse.db"), initial_cash=100)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    paper.convert_currency(
        "EUR", "USD", 50, 1.1, 0, "fund-usd", "twelve_data", timestamp,
    )

    class FakeFxProvider:
        def exchange_rate(self, symbol):
            assert symbol == "EUR/USD"
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    monkeypatch.setattr("app.main.portfolio", paper)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: FakeFxProvider())
    response = client.post(
        "/api/agent/fx-conversions",
        json={
            "from_currency": "USD", "to_currency": "EUR", "amount": 10,
            "client_conversion_id": "return-usd-to-eur",
        },
        headers=headers,
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["rate"] == pytest.approx(1 / 1.1)
    assert payload["net_to_amount"] == pytest.approx((10 / 1.1) * 0.999)
    assert payload["fee_eur"] == pytest.approx(10 / 1.1 * 0.001)


def test_order_intent_history_api_requires_owner_token_and_returns_audit_events(monkeypatch, tmp_path):
    paper = PaperPortfolio(str(tmp_path / "order-intents-api.db"), initial_cash=20)
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "api-order-intent",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": "ema_12_26", "stop_loss": 9800, "take_profit": 10400,
    }
    paper.record_order_intent("api-order-intent", request, {"risk_accepted": True})
    monkeypatch.setattr("app.main.portfolio", paper)
    headers = configure_order_auth(monkeypatch)

    unauthorized = client.get(
        "/api/agent/order-intents",
        params={"client_order_id": "api-order-intent"},
    )
    assert unauthorized.status_code == 401

    fill = paper.execute(**paper.get_order_intent("api-order-intent")["request"])
    response = client.get(
        "/api/agent/order-intents",
        params={"client_order_id": "api-order-intent"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["status"] == "filled"
    assert payload["item"]["trade_id"] == fill["id"]
    assert [event["to_status"] for event in payload["events"]] == ["risk_approved", "filled"]


def test_manual_crypto_order_replay_returns_original_fill_without_new_quote(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    headers = configure_order_auth(monkeypatch)
    paper = PaperPortfolio(str(tmp_path / "crypto-order-replay.db"), initial_cash=100)
    monkeypatch.setattr("app.main.portfolio", paper)
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    calls = []

    def ticker(symbol, provider_id=None):
        calls.append(symbol)
        last = 10000 if len(calls) == 1 else 11000
        return {
            "symbol": symbol, "last": last, "bid": last - 1, "ask": last + 1,
            "timestamp": timestamp, "provider_id": provider_id,
        }

    monkeypatch.setattr("app.main.market.ticker", ticker)
    payload = {
        "symbol": "BTC/EUR", "provider_id": "ccxt:kraken",
        "side": "buy", "quantity": 0.001, "stop_loss": 9800,
        "client_order_id": "crypto-manual-replay",
    }
    first = client.post("/api/orders", json=payload, headers=headers)
    replay = client.post("/api/orders", json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["idempotent_replay"] is True
    assert len(calls) == 1
    assert len(paper.history()) == 1
    assert [event["to_status"] for event in paper.list_order_intent_events("crypto-manual-replay")] == [
        "risk_approved", "filled",
    ]


def test_backup_status_endpoint_requires_owner_token_and_reports_offsite_state(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr("app.main.settings.backup_directory", str(tmp_path))
    (tmp_path / "backup_status.json").write_text(
        json.dumps({
            "last_cycle_at": "2026-11-10T00:00:00+00:00",
            "local": {"status": "created", "created": ["trading.sqlite3"], "failures": []},
            "external": {
                "enabled": True, "status": "verified",
                "uploaded": [{"key": "private/trading.sqlite3", "verified": True}],
                "failures": [],
            },
        }),
        encoding="utf-8",
    )
    headers = configure_order_auth(monkeypatch)
    unauthorized = client.get("/api/agent/backup-status")
    assert unauthorized.status_code == 401

    response = client.get("/api/agent/backup-status", headers=headers)
    assert response.status_code == 200
    assert response.json()["external"]["status"] == "verified"


def test_dashboard_chart_ohlcv_route_accepts_encoded_slash_symbol(monkeypatch):
    calls = []

    def fake_ohlcv(symbol, timeframe="1h", limit=100, provider_id=None):
        calls.append((symbol, timeframe, limit, provider_id))
        return [[1731234567000, 100, 105, 98, 103, 12.5]]

    monkeypatch.setattr("app.main.market.ohlcv", fake_ohlcv)
    response = client.get(
        "/api/market/BTC%2FEUR/ohlcv?timeframe=1h&limit=120&provider_id=ccxt%3Akraken"
    )

    assert response.status_code == 200, response.text
    assert calls == [("BTC/EUR", "1h", 120, "ccxt:kraken")]
    assert response.json()["candles"][0]["close"] == 103


def test_markets_api_exposes_normalized_instrument_contract_fields(monkeypatch, tmp_path):
    from app.providers.base import AssetClass, MarketInstrument, MarketType, PrecisionMode

    instrument = MarketInstrument(
        provider_id="ccxt:example", symbol="BTC/EUR",
        asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
        base="BTC", quote="EUR", active=True,
        venue_id="example", native_symbol="BTC/EUR",
        precision_mode=PrecisionMode.TICK_SIZE,
        price_tick=0.01, amount_step=0.0001,
        min_amount=0.00001, max_amount=2,
        min_cost=5, max_cost=10000,
        contract_type="spot", trading_status="active",
    )
    from app.instrument_catalog import InstrumentCatalog
    monkeypatch.setattr("app.main.instrument_catalog", InstrumentCatalog(str(tmp_path / "markets.db")))
    monkeypatch.setattr(
        "app.main.market.discover_markets",
        lambda: ([instrument], [{"provider_id": "bad-provider", "reason": "api_key=must-not-leak"}]),
    )

    response = client.get("/api/markets")

    assert response.status_code == 200
    item = response.json()["instruments"][0]
    assert item["venue_id"] == "example"
    assert item["native_symbol"] == "BTC/EUR"
    assert item["precision_mode"] == "tick_size"
    assert item["price_tick"] == pytest.approx(0.01)
    assert item["amount_step"] == pytest.approx(0.0001)
    assert item["maximum_amount"] == pytest.approx(2)
    assert item["trading_status"] == "active"
    assert response.json()["catalogue_sync"]["inserted_count"] == 1
    assert response.json()["catalogue_sync"]["status"] == "partial"
    assert "must-not-leak" not in response.text
    assert response.json()["provider_failures"][0]["reason"].endswith("[REDACTED]")
    assert item["catalogue_id"].startswith("inst_")


def test_instrument_catalogue_api_lists_filters_and_returns_detail(monkeypatch, tmp_path):
    from app.instrument_catalog import InstrumentCatalog
    from app.providers.base import AssetClass, MarketInstrument, MarketType

    catalogue = InstrumentCatalog(str(tmp_path / "api-catalogue.db"))
    item = MarketInstrument(
        provider_id="twelve_data", symbol="ACME",
        asset_class=AssetClass.EQUITY, market_type=MarketType.CASH_EQUITY,
        base="ACME", quote="USD", active=True,
        venue_id="XNAS", native_symbol="ACME",
    )
    catalogue.record_discovery([item])
    monkeypatch.setattr("app.main.instrument_catalog", catalogue)

    listing = client.get("/api/instruments?asset_class=equity&provider_id=twelve_data")
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1
    venue_listing = client.get("/api/instruments?venue_id=XNAS")
    assert venue_listing.status_code == 200
    assert len(venue_listing.json()["items"]) == 1
    catalogue_id = listing.json()["items"][0]["catalogue_id"]

    detail = client.get(f"/api/instruments/{catalogue_id}")
    assert detail.status_code == 200
    assert detail.json()["venue_id"] == "XNAS"
    assert detail.json()["instrument_id"] == "twelve_data:cash_equity:ACME"

    missing = client.get("/api/instruments/inst_missing")
    assert missing.status_code == 404


def test_instrument_catalogue_refresh_requires_owner_token(monkeypatch, tmp_path):
    from app.instrument_catalog import InstrumentCatalog

    monkeypatch.setattr("app.main.instrument_catalog", InstrumentCatalog(str(tmp_path / "refresh.db")))
    monkeypatch.setattr("app.main.market.discover_markets", lambda: ([], []))
    monkeypatch.setattr("app.main.settings.agent_control_token", "test-control-token")

    unauthorized = client.post("/api/instruments/refresh")
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/api/instruments/refresh",
        headers={"X-Agent-Token": "test-control-token"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["status"] == "success"
    assert authorized.json()["discovered_count"] == 0


def test_instrument_discovery_runs_endpoint_returns_persisted_failures(monkeypatch, tmp_path):
    from app.instrument_catalog import InstrumentCatalog

    catalogue = InstrumentCatalog(str(tmp_path / "runs.db"))
    catalogue.record_discovery([], [{"provider_id": "ccxt:kraken", "reason": "offline"}])
    monkeypatch.setattr("app.main.instrument_catalog", catalogue)

    response = client.get("/api/instrument-discovery/runs")

    assert response.status_code == 200
    assert response.json()["items"][0]["failures"][0]["reason"] == "offline"


def test_binance_account_endpoints_require_owner_auth_and_expose_only_read_capabilities(monkeypatch):
    headers = configure_order_auth(monkeypatch)

    class FakeReadOnlyAccount:
        def describe(self):
            return {
                "provider_id": "ccxt:binance",
                "account_alias": "binance-spot-readonly",
                "read_only_connector": True,
                "order_submission": False,
                "withdrawals": False,
                "account_key_permissions_verified": False,
            }

        def fetch_balances(self):
            return {
                "provider_id": "ccxt:binance",
                "account_alias": "binance-spot-readonly",
                "market_type": "spot",
                "read_only": True,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "balances": [{"asset": "BTC", "free": 0.1, "used": 0.0, "total": 0.1}],
                "balance_count": 1,
                "execution_enabled": False,
                "withdrawals_enabled": False,
            }

    monkeypatch.setattr("app.main.binance_account_provider", FakeReadOnlyAccount())
    monkeypatch.setattr("app.main.binance_account_configuration_status", "configured")

    unauthorized = client.get("/api/accounts")
    assert unauthorized.status_code == 401
    unauthorized_balances = client.get("/api/accounts/binance/spot/balances")
    assert unauthorized_balances.status_code == 401

    accounts = client.get("/api/accounts", headers=headers)
    assert accounts.status_code == 200
    assert accounts.json()["configuration_status"] == "configured"
    assert accounts.json()["accounts"][0]["order_submission"] is False
    assert accounts.json()["accounts"][0]["withdrawals"] is False

    balances = client.get("/api/accounts/binance/spot/balances", headers=headers)
    assert balances.status_code == 200
    assert balances.json()["balances"][0]["asset"] == "BTC"
    assert balances.json()["execution_enabled"] is False
    assert "api_key" not in balances.text.lower()
    assert "secret" not in balances.text.lower()


def test_binance_account_balances_endpoint_is_disabled_without_credentials(monkeypatch):
    headers = configure_order_auth(monkeypatch)
    monkeypatch.setattr("app.main.binance_account_provider", None)
    response = client.get("/api/accounts/binance/spot/balances", headers=headers)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_binance_account_endpoint_sanitizes_connector_failures(monkeypatch):
    from app.accounts.binance_spot import AccountConnectorError

    headers = configure_order_auth(monkeypatch)

    class FailingReadOnlyAccount:
        def fetch_balances(self):
            raise AccountConnectorError("Binance spot balance request failed (NetworkError)")

    monkeypatch.setattr("app.main.binance_account_provider", FailingReadOnlyAccount())
    response = client.get("/api/accounts/binance/spot/balances", headers=headers)
    assert response.status_code == 502
    assert "NetworkError" in response.json()["detail"]
    assert "secret" not in response.text.lower()


def test_binance_account_snapshot_api_persists_and_reconciles_balances(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from app.accounts.snapshot_store import AccountSnapshotStore

    headers = configure_order_auth(monkeypatch)
    store = AccountSnapshotStore(str(tmp_path / "account-snapshots.db"))
    monkeypatch.setattr("app.main.account_snapshot_store", store)

    class FakeReadOnlyAccount:
        def __init__(self):
            self.calls = 0

        def fetch_balances(self):
            self.calls += 1
            fetched = datetime.now(timezone.utc) + timedelta(milliseconds=self.calls)
            btc = "0.1" if self.calls == 1 else "0.15"
            return {
                "provider_id": "ccxt:binance",
                "account_alias": "binance-spot-readonly",
                "market_type": "spot",
                "read_only": True,
                "fetched_at": fetched.isoformat(),
                "balances": [
                    {"asset": "BTC", "free": btc, "used": "0", "total": btc},
                    {"asset": "EUR", "free": "20", "used": "0", "total": "20"},
                ],
                "balance_count": 2,
                "execution_enabled": False,
                "withdrawals_enabled": False,
            }

    provider = FakeReadOnlyAccount()
    monkeypatch.setattr("app.main.binance_account_provider", provider)

    unauthorized = client.post("/api/accounts/binance/spot/snapshots")
    assert unauthorized.status_code == 401

    first = client.post("/api/accounts/binance/spot/snapshots", headers=headers)
    second = client.post("/api/accounts/binance/spot/snapshots", headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["reconciliation_status"] == "initial"
    assert second.json()["reconciliation_status"] == "changed"
    assert second.json()["previous_snapshot_id"] == first.json()["snapshot_id"]
    btc_change = next(item for item in second.json()["changes"] if item["asset"] == "BTC")
    assert btc_change["delta_total"] == "0.05"

    unauthorized_history = client.get("/api/accounts/snapshots")
    assert unauthorized_history.status_code == 401
    history = client.get("/api/accounts/snapshots?provider_id=ccxt%3Abinance", headers=headers)
    assert history.status_code == 200
    assert len(history.json()["items"]) == 2

    detail = client.get(
        f"/api/accounts/snapshots/{second.json()['snapshot_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["balances"][0]["asset"] == "BTC"
    assert detail.json()["changes"]


def test_binance_account_snapshot_api_rejects_invalid_provider_snapshot(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.accounts.snapshot_store import AccountSnapshotStore

    headers = configure_order_auth(monkeypatch)
    monkeypatch.setattr(
        "app.main.account_snapshot_store",
        AccountSnapshotStore(str(tmp_path / "invalid-snapshot.db")),
    )

    class InvalidReadOnlyAccount:
        def fetch_balances(self):
            return {
                "provider_id": "ccxt:binance",
                "account_alias": "binance-spot-readonly",
                "market_type": "spot",
                "read_only": True,
                "execution_enabled": False,
                "withdrawals_enabled": False,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "balances": [{"asset": "BTC", "free": 0.1, "used": "0", "total": "0.1"}],
            }

    monkeypatch.setattr("app.main.binance_account_provider", InvalidReadOnlyAccount())
    response = client.post("/api/accounts/binance/spot/snapshots", headers=headers)
    assert response.status_code == 502
    assert "invalid account snapshot" in response.json()["detail"]
    assert AccountSnapshotStore(str(tmp_path / "invalid-snapshot.db")).list_snapshots() == []


def test_binance_account_snapshot_api_returns_404_for_unknown_snapshot(monkeypatch):
    headers = configure_order_auth(monkeypatch)
    response = client.get("/api/accounts/snapshots/unknown", headers=headers)
    assert response.status_code == 404


def test_binance_demo_endpoints_require_owner_auth_and_are_explicitly_demo_only(monkeypatch):
    headers = configure_order_auth(monkeypatch)

    class FakeDemoAccount:
        def describe(self):
            return {
                "provider_id": "ccxt:binance-demo",
                "environment": "demo",
                "demo_only": True,
                "order_submission": False,
                "production_endpoint_allowed": False,
            }

        def fetch_balances(self):
            return {
                "provider_id": "ccxt:binance-demo",
                "environment": "demo",
                "read_only": True,
                "execution_enabled": False,
                "withdrawals_enabled": False,
                "balances": [{"asset": "BTC", "free": "1", "used": "0", "total": "1"}],
            }

        def discover_spot_markets(self, limit=100):
            return {
                "provider_id": "ccxt:binance-demo",
                "environment": "demo",
                "demo_only": True,
                "discovered": 1,
                "markets": [{"symbol": "BTC/USDT"}],
            }

    monkeypatch.setattr("app.main.binance_demo_account_provider", FakeDemoAccount())
    monkeypatch.setattr("app.main.binance_demo_configuration_status", "configured")

    unauthorized = client.get("/api/demo/binance/spot/balances")
    assert unauthorized.status_code == 401
    unauthorized_markets = client.get("/api/demo/binance/spot/markets")
    assert unauthorized_markets.status_code == 401

    balances = client.get("/api/demo/binance/spot/balances", headers=headers)
    assert balances.status_code == 200
    assert balances.json()["environment"] == "demo"
    assert balances.json()["execution_enabled"] is False
    assert balances.json()["withdrawals_enabled"] is False

    markets = client.get("/api/demo/binance/spot/markets?limit=10", headers=headers)
    assert markets.status_code == 200
    assert markets.json()["demo_only"] is True
    assert markets.json()["markets"][0]["symbol"] == "BTC/USDT"

    accounts = client.get("/api/accounts", headers=headers)
    assert accounts.status_code == 200
    assert accounts.json()["demo_configuration_status"] == "configured"
    assert any(
        item["provider_id"] == "ccxt:binance-demo"
        for item in accounts.json()["accounts"]
    )


def test_binance_demo_endpoints_are_disabled_by_default(monkeypatch):
    headers = configure_order_auth(monkeypatch)
    monkeypatch.setattr("app.main.binance_demo_account_provider", None)
    monkeypatch.setattr("app.main.binance_demo_configuration_status", "disabled")

    balances = client.get("/api/demo/binance/spot/balances", headers=headers)
    markets = client.get("/api/demo/binance/spot/markets", headers=headers)
    assert balances.status_code == 503
    assert markets.status_code == 503
    assert "not configured" in balances.json()["detail"]


def test_binance_demo_account_configuration_uses_masked_secret_settings():
    from app.config import Settings

    api_key = "demo-only-api-key"
    api_secret = "demo-only-api-secret"
    config = Settings(
        _env_file=None,
        binance_spot_demo_enabled=True,
        binance_spot_demo_api_key=api_key,
        binance_spot_demo_api_secret=api_secret,
    )
    assert config.binance_spot_demo_api_key.get_secret_value() == api_key
    assert config.binance_spot_demo_api_secret.get_secret_value() == api_secret
    assert api_key not in repr(config)
    assert api_secret not in repr(config)


def test_binance_demo_order_api_persists_before_submission_and_is_idempotent(monkeypatch, tmp_path):
    from app.accounts.demo_order_store import BinanceDemoOrderStore

    headers = configure_order_auth(monkeypatch)
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    monkeypatch.setattr("app.main.demo_order_store", store)

    class FakeDemoExecution:
        def __init__(self):
            self.submissions = 0

        def validate_limit_order(self, **kwargs):
            return {
                "client_order_id": kwargs["client_order_id"],
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "amount": kwargs["amount"],
                "price": kwargs["price"],
                "quote_asset": "USDT",
            }

        def submit_limit_order(self, request):
            self.submissions += 1
            assert store.get_order(request["client_order_id"])["status"] == "submission_unknown"
            return {
                "status": "open",
                "exchange_order_id": "demo-exchange-1",
                "exchange_status": "new",
                "filled": "0",
                "remaining": request["amount"],
                "average": None,
                "error": None,
            }

    provider = FakeDemoExecution()
    monkeypatch.setattr("app.main.binance_demo_account_provider", provider)
    payload = {
        "symbol": "BTC/USDT", "side": "buy", "amount": "0.001",
        "price": "20000", "client_order_id": "api-demo-order-1",
    }

    unauthorized = client.post("/api/demo/binance/spot/orders", json=payload)
    assert unauthorized.status_code == 401

    created = client.post("/api/demo/binance/spot/orders", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "open"
    assert created.json()["exchange_order_id"] == "demo-exchange-1"
    assert provider.submissions == 1

    replay = client.post("/api/demo/binance/spot/orders", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["client_order_id"] == "api-demo-order-1"
    assert provider.submissions == 1

    conflict = client.post(
        "/api/demo/binance/spot/orders",
        json={**payload, "amount": "0.002"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert provider.submissions == 1

    events = client.get(
        "/api/demo/binance/spot/orders/api-demo-order-1/events",
        headers=headers,
    )
    assert events.status_code == 200
    assert [item["to_status"] for item in events.json()["items"]] == [
        "intent_created", "submission_unknown", "open",
    ]


def test_binance_demo_order_api_never_resubmits_unknown_submission_and_reconciles_by_client_id(monkeypatch, tmp_path):
    from app.accounts.demo_order_store import BinanceDemoOrderStore
    from app.accounts.binance_demo import BinanceDemoError

    headers = configure_order_auth(monkeypatch)
    store = BinanceDemoOrderStore(str(tmp_path / "demo-unknown.db"))
    monkeypatch.setattr("app.main.demo_order_store", store)

    class FakeDemoExecution:
        def __init__(self):
            self.submissions = 0
            self.reconciliations = 0

        def validate_limit_order(self, **kwargs):
            return {
                "client_order_id": kwargs["client_order_id"],
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "amount": kwargs["amount"],
                "price": kwargs["price"],
                "quote_asset": "USDT",
            }

        def submit_limit_order(self, request):
            self.submissions += 1
            raise BinanceDemoError("Binance Demo order submission outcome is unknown (NetworkError)")

        def fetch_order_by_client_id(self, *, symbol, client_order_id, expected_amount=None, expected_side=None):
            self.reconciliations += 1
            return {
                "status": "filled",
                "exchange_order_id": "demo-exchange-unknown",
                "exchange_status": "closed",
                "filled": "0.001",
                "remaining": "0",
                "average": "20000",
                "error": None,
            }

    provider = FakeDemoExecution()
    monkeypatch.setattr("app.main.binance_demo_account_provider", provider)
    payload = {
        "symbol": "BTC/USDT", "side": "buy", "amount": "0.001",
        "price": "20000", "client_order_id": "api-demo-unknown-1",
    }

    first = client.post("/api/demo/binance/spot/orders", json=payload, headers=headers)
    assert first.status_code == 202
    assert first.json()["status"] == "submission_unknown"
    assert provider.submissions == 1

    replay = client.post("/api/demo/binance/spot/orders", json=payload, headers=headers)
    assert replay.status_code == 202
    assert provider.submissions == 1

    reconciled = client.post(
        "/api/demo/binance/spot/orders/api-demo-unknown-1/reconcile",
        headers=headers,
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "filled"
    assert provider.submissions == 1
    assert provider.reconciliations == 1


def test_binance_demo_order_api_cancels_open_order_and_blocks_second_unresolved_order(monkeypatch, tmp_path):
    from app.accounts.demo_order_store import BinanceDemoOrderStore

    headers = configure_order_auth(monkeypatch)
    store = BinanceDemoOrderStore(str(tmp_path / "demo-cancel.db"))
    monkeypatch.setattr("app.main.demo_order_store", store)

    class FakeDemoExecution:
        def validate_limit_order(self, **kwargs):
            return {
                "client_order_id": kwargs["client_order_id"],
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "amount": kwargs["amount"],
                "price": kwargs["price"],
                "quote_asset": "USDT",
            }

        def submit_limit_order(self, request):
            return {
                "status": "open", "exchange_order_id": "cancel-id-1",
                "exchange_status": "new", "filled": "0",
                "remaining": request["amount"], "average": None, "error": None,
            }

        def cancel_limit_order(self, **kwargs):
            return {
                "status": "cancelled", "exchange_order_id": kwargs["exchange_order_id"],
                "exchange_status": "canceled", "filled": "0",
                "remaining": "0.001", "average": None, "error": None,
            }

    monkeypatch.setattr("app.main.binance_demo_account_provider", FakeDemoExecution())
    payload = {
        "symbol": "BTC/USDT", "side": "buy", "amount": "0.001",
        "price": "20000", "client_order_id": "api-demo-cancel-1",
    }
    created = client.post("/api/demo/binance/spot/orders", json=payload, headers=headers)
    assert created.status_code == 201

    blocked = client.post(
        "/api/demo/binance/spot/orders",
        json={**payload, "client_order_id": "api-demo-cancel-2"},
        headers=headers,
    )
    assert blocked.status_code == 409

    cancelled = client.post(
        "/api/demo/binance/spot/orders/api-demo-cancel-1/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_binance_demo_order_api_is_disabled_without_demo_connector(monkeypatch):
    headers = configure_order_auth(monkeypatch)
    monkeypatch.setattr("app.main.binance_demo_account_provider", None)
    response = client.post(
        "/api/demo/binance/spot/orders",
        json={
            "symbol": "BTC/USDT", "side": "buy", "amount": "0.001",
            "price": "20000", "client_order_id": "disabled-demo-order",
        },
        headers=headers,
    )
    assert response.status_code == 503


def test_binance_demo_order_api_requires_exact_decimal_strings(monkeypatch):
    headers = configure_order_auth(monkeypatch)
    payload = {
        "symbol": "BTC/USDT", "side": "buy", "amount": 0.001,
        "price": "20000", "client_order_id": "numeric-demo-amount",
    }
    response = client.post(
        "/api/demo/binance/spot/orders",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 422
    assert "amount" in response.text

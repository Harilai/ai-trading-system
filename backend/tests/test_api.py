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

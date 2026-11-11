from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.historical_data import HistoricalCandleStore


client = TestClient(app)


def auth(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "history-test-token")
    return {"X-Agent-Token": "history-test-token"}


def test_history_sync_endpoint_paginates_and_persists_provider_scoped_candles(monkeypatch, tmp_path):
    headers = auth(monkeypatch)
    store = HistoricalCandleStore(str(tmp_path / "history.sqlite"))
    monkeypatch.setattr("app.main.historical_candle_store", store)
    monkeypatch.setattr("app.main.historical_candle_collector", __import__(
        "app.historical_data", fromlist=["PaginatedCandleCollector"]
    ).PaginatedCandleCollector(store))

    rows = [
        [1000, 10, 12, 9, 11, 100],
        [2000, 11, 13, 10, 12, 110],
        [3000, 12, 14, 11, 13, 120],
    ]
    calls = []

    class Exchange:
        @staticmethod
        def parse_timeframe(value):
            assert value == "1s"
            return 1

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            calls.append(since)
            return [row for row in rows if row[0] >= since][:2]

    provider = SimpleNamespace(
        exchange=Exchange(),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True, instrument_id="ccxt:kraken:spot:BTC/EUR"
        )],
    )
    monkeypatch.setattr("app.main.market.get", lambda provider_id: provider)

    response = client.post(
        "/api/market-data/history/sync",
        params={
            "provider_id": "ccxt:kraken", "symbol": "BTC/EUR", "timeframe": "1s",
            "since_ms": 1000, "until_ms": 4000,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["complete"] is True
    assert payload["inserted"] == 3
    assert calls == [1000, 3000]

    fetched = client.get(
        "/api/market-data/history",
        params={
            "provider_id": "ccxt:kraken",
            "instrument_id": "ccxt:kraken:spot:BTC/EUR",
            "timeframe": "1s",
        },
        headers=headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["count"] == 3


def test_history_endpoints_require_control_token(monkeypatch):
    monkeypatch.setattr("app.main.settings.agent_control_token", "history-test-token")
    response = client.get(
        "/api/market-data/history",
        params={
            "provider_id": "p", "instrument_id": "i", "timeframe": "1m",
        },
    )
    assert response.status_code == 401


def test_history_sync_rejects_provider_without_paginated_ohlcv(monkeypatch):
    headers = auth(monkeypatch)
    monkeypatch.setattr("app.main.market.get", lambda provider_id: SimpleNamespace())
    response = client.post(
        "/api/market-data/history/sync",
        params={
            "provider_id": "provider", "symbol": "BTC/EUR", "timeframe": "1h",
            "since_ms": 1000, "until_ms": 2000,
        },
        headers=headers,
    )
    assert response.status_code == 409

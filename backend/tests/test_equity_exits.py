import time

from app.equity_exits import EquityExitMonitor
from app.portfolio import PaperPortfolio
from app.providers.base import AssetClass, MarketInstrument, MarketType


def _position(**updates):
    result = {
        "provider_id": "twelve_data",
        "instrument_id": "twelve_data:cash_equity:AAPL",
        "symbol": "AAPL",
        "quantity": 2.0,
        "average_price": 100.0,
        "strategy_id": "demo",
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "quote_currency": "USD",
    }
    result.update(updates)
    return result


class FakePortfolio:
    def __init__(self, position):
        self.position = position
        self.executions = []

    def snapshot(self):
        return {"positions": [self.position] if self.position else []}

    def execute(self, *args, **kwargs):
        self.executions.append((args, kwargs))
        return {"symbol": args[0], "side": args[1], "mode": "paper"}


class FakeProvider:
    provider_id = "twelve_data"

    def __init__(self, price, *, market_open=True, timestamp=None, currency="USD", fx_timestamp=None):
        self.price = price
        self.market_open = market_open
        self.timestamp = timestamp or int(time.time() * 1000)
        self.currency = currency
        self.fx_timestamp = fx_timestamp or int(time.time() * 1000)

    def exchange_rate(self, symbol):
        return {
            "symbol": symbol,
            "rate": 1.1,
            "timestamp": self.fx_timestamp,
            "provider_id": "twelve_data",
        }

    def discover_markets(self, asset_classes):
        assert set(asset_classes) == {AssetClass.EQUITY, AssetClass.ETF}
        return [MarketInstrument(
            provider_id="twelve_data", symbol="AAPL", asset_class=AssetClass.EQUITY,
            market_type=MarketType.CASH_EQUITY, base="AAPL", quote="USD", active=True,
            metadata={"exchange": "NASDAQ", "mic_code": "XNAS"},
        )]

    def ticker(self, symbol):
        return {
            "symbol": symbol, "last": self.price, "timestamp": self.timestamp,
            "provider_id": "twelve_data", "currency": self.currency,
            "exchange": "NASDAQ", "mic_code": "XNAS",
            "is_market_open": self.market_open,
        }

    def market_state(self):
        return {}


def test_stop_loss_closes_paper_position_with_fresh_market_and_fx():
    portfolio = FakePortfolio(_position())
    monitor = EquityExitMonitor(FakeProvider(89.5), portfolio)

    result = monitor.run_once()

    assert result["checked"] == 1
    assert len(result["closed"]) == 1
    assert result["closed"][0]["reason"] == "stop_loss"
    args, order = portfolio.executions[0]
    assert args[:4] == ("AAPL", "sell", 2.0, 89.5)
    assert order["quote_currency"] == "USD"
    assert order["fx_rate_to_eur"] == 1 / 1.1
    assert order["client_order_id"].startswith("equity-exit-")
    assert args[7] == 0.0


def test_take_profit_uses_bid_when_available():
    portfolio = FakePortfolio(_position())
    provider = FakeProvider(125)
    provider.ticker = lambda symbol: {
        "symbol": symbol, "last": 125, "bid": 124.8, "ask": 125.0,
        "timestamp": int(time.time() * 1000), "provider_id": "twelve_data",
        "currency": "USD", "exchange": "NASDAQ", "mic_code": "XNAS",
        "is_market_open": True,
    }
    result = EquityExitMonitor(provider, portfolio).run_once()

    assert result["closed"][0]["reason"] == "take_profit"
    assert portfolio.executions[0][0][3] == 124.8


def test_monitor_fails_closed_for_stale_quote_or_closed_session():
    old = int(time.time() * 1000) - 120_000
    for provider in (FakeProvider(89, timestamp=old), FakeProvider(89, market_open=False)):
        portfolio = FakePortfolio(_position())
        result = EquityExitMonitor(provider, portfolio).run_once()
        assert result["closed"] == []
        assert result["skipped"]
        assert portfolio.executions == []


def test_monitor_fails_closed_for_non_usd_quote_and_is_inactive_without_levels():
    portfolio = FakePortfolio(_position())
    result = EquityExitMonitor(FakeProvider(89, currency="EUR"), portfolio).run_once()
    assert result["closed"] == []
    assert result["skipped"]
    assert portfolio.executions == []

    no_levels = FakePortfolio(_position(stop_loss=None, take_profit=None))
    result = EquityExitMonitor(FakeProvider(89), no_levels).run_once()
    assert result["checked"] == 0
    assert result["closed"] == []


def test_monitor_fails_closed_when_fx_is_stale():
    portfolio = FakePortfolio(_position())
    stale_fx = int(time.time() * 1000) - 600_000
    result = EquityExitMonitor(FakeProvider(89, fx_timestamp=stale_fx), portfolio).run_once()
    assert result["closed"] == []
    assert result["skipped"]
    assert portfolio.executions == []


def test_exit_is_a_real_paper_ledger_close_and_recovers_after_restart(tmp_path):
    now = int(time.time() * 1000)
    portfolio = PaperPortfolio(str(tmp_path / "equity-exit.db"), initial_cash=300)
    portfolio.convert_currency("EUR", "USD", 200, 1.1, 0, "fund-usd", "twelve_data", now)
    portfolio.execute(
        "AAPL", "buy", 1, 100, 0.001, 0, 1, 1,
        provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
        strategy_id="demo", quote_currency="USD", fx_rate_to_eur=1 / 1.1,
        fx_provider_id="twelve_data", fx_timestamp=now, market_timestamp=now,
        stop_loss=90, take_profit=120,
    )
    provider = FakeProvider(89)

    first = EquityExitMonitor(provider, portfolio).run_once()
    restarted = PaperPortfolio(str(tmp_path / "equity-exit.db"), initial_cash=300)
    second = EquityExitMonitor(provider, restarted).run_once()

    assert len(first["closed"]) == 1
    assert restarted.snapshot()["positions"] == []
    trades = restarted.history(10)
    assert [trade["side"] for trade in trades] == ["sell", "buy"]
    assert trades[0]["client_order_id"].startswith("equity-exit-")
    assert second["checked"] == 0
    assert second["closed"] == []


def test_missing_market_open_uses_provider_session_state_and_fails_closed():
    portfolio = FakePortfolio(_position())
    provider = FakeProvider(89, market_open=None)
    provider.market_state = lambda: {}
    result = EquityExitMonitor(provider, portfolio).run_once()
    assert result["closed"] == []
    assert result["skipped"]
    assert portfolio.executions == []


def test_monitor_rejects_malformed_market_session_flag():
    portfolio = FakePortfolio(_position())
    provider = FakeProvider(89)
    provider.ticker = lambda symbol: {
        "symbol": symbol, "last": 89, "timestamp": int(time.time() * 1000),
        "provider_id": "twelve_data", "currency": "USD",
        "exchange": "NASDAQ", "mic_code": "XNAS", "is_market_open": "false",
    }
    result = EquityExitMonitor(provider, portfolio).run_once()
    assert result["closed"] == []
    assert result["skipped"]
    assert portfolio.executions == []


def test_instrument_discovery_is_cached_between_minute_checks():
    portfolio = FakePortfolio(_position())
    provider = FakeProvider(89)
    calls = {"count": 0}
    original = provider.discover_markets

    def discover(asset_classes):
        calls["count"] += 1
        return original(asset_classes)

    provider.discover_markets = discover
    monitor = EquityExitMonitor(provider, portfolio)
    monitor.run_once()
    monitor.run_once()
    assert calls["count"] == 1


def test_monitor_rejects_quote_from_different_venue():
    portfolio = FakePortfolio(_position())
    provider = FakeProvider(89)
    original = provider.ticker

    def wrong_venue(symbol):
        quote = original(symbol)
        quote["mic_code"] = "XNYS"
        quote["exchange"] = "NYSE"
        return quote

    provider.ticker = wrong_venue
    result = EquityExitMonitor(provider, portfolio).run_once()
    assert result["closed"] == []
    assert result["skipped"]
    assert portfolio.executions == []

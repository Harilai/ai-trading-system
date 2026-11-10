from decimal import Decimal

import pytest

from app.accounts.ccxt_readonly import CcxtReadOnlyAccount, CcxtReadOnlyAccountError
from app.accounts.contracts import AccountProviderRegistry


class FakeExchange:
    has = {"fetchBalance": True}
    rateLimit = 250

    def __init__(self, response=None, error=None):
        self.response = response or {
            "free": {"BTC": 0.125, "USDT": 50.0},
            "used": {"BTC": 0.025, "USDT": 0.0},
            "total": {"BTC": 0.15, "USDT": 50.0},
        }
        self.error = error
        self.calls = []

    def fetch_balance(self):
        self.calls.append("fetch_balance")
        if self.error:
            raise self.error
        return self.response


def test_ccxt_readonly_adapter_configures_spot_and_only_exposes_balance_reads():
    exchange = FakeExchange()
    captured = {}

    def factory(options):
        captured.update(options)
        return exchange

    provider = CcxtReadOnlyAccount(
        "kraken", "api-key-secret", "api-secret-secret",
        exchange_factory=factory,
    )
    description = provider.describe()
    snapshot = provider.fetch_balances()

    assert captured["apiKey"] == "api-key-secret"
    assert captured["secret"] == "api-secret-secret"
    assert captured["options"]["defaultType"] == "spot"
    assert captured["enableRateLimit"] is True
    assert provider.provider_id == "ccxt:kraken"
    assert description["environment"] == "production"
    assert description["read_only_connector"] is True
    assert description["order_submission"] is False
    assert description["order_cancellation"] is False
    assert description["transfers"] is False
    assert description["withdrawals"] is False
    assert description["wallet_signing"] is False
    assert description["account_key_permissions_verified"] is False
    assert "api-key-secret" not in str(description)
    assert "api-secret-secret" not in str(description)
    assert exchange.calls == ["fetch_balance"]
    assert snapshot["read_only"] is True
    assert snapshot["execution_enabled"] is False
    assert snapshot["withdrawals_enabled"] is False
    assert snapshot["transfers_enabled"] is False
    assert snapshot["balances"] == [
        {"asset": "BTC", "free": "0.125", "used": "0.025", "total": "0.15"},
        {"asset": "USDT", "free": "50", "used": "0", "total": "50"},
    ]


def test_ccxt_readonly_adapter_registers_with_normalized_account_registry():
    exchange = FakeExchange()
    provider = CcxtReadOnlyAccount(
        "kraken", "key", "secret", exchange_factory=lambda _options: exchange,
    )
    registry = AccountProviderRegistry()
    registry.register(provider)

    accounts = registry.list_accounts()
    snapshot = registry.fetch_balances("ccxt:kraken")

    assert accounts[0]["account_alias"] == "kraken-spot-readonly"
    assert snapshot["environment"] == "production"
    assert snapshot["permissions_verified"] is False
    assert snapshot["balances"][0]["asset"] == "BTC"


def test_ccxt_readonly_adapter_rejects_missing_or_invalid_credentials():
    with pytest.raises(CcxtReadOnlyAccountError, match="API key"):
        CcxtReadOnlyAccount("kraken", "", "secret", exchange_factory=lambda _: FakeExchange())
    with pytest.raises(CcxtReadOnlyAccountError, match="API secret"):
        CcxtReadOnlyAccount("kraken", "key", "", exchange_factory=lambda _: FakeExchange())
    with pytest.raises(CcxtReadOnlyAccountError, match="exchange ID"):
        CcxtReadOnlyAccount("../kraken", "key", "secret", exchange_factory=lambda _: FakeExchange())


def test_ccxt_readonly_adapter_rejects_exchanges_without_balance_support():
    exchange = FakeExchange()
    exchange.has = {"fetchBalance": False}
    with pytest.raises(CcxtReadOnlyAccountError, match="does not support"):
        CcxtReadOnlyAccount("kraken", "key", "secret", exchange_factory=lambda _: exchange)


def test_ccxt_readonly_adapter_rejects_malformed_and_negative_balances():
    exchange = FakeExchange(response={
        "free": {"BTC": -1},
        "used": {"BTC": 0},
        "total": {"BTC": -1},
    })
    provider = CcxtReadOnlyAccount(
        "kraken", "key", "secret", exchange_factory=lambda _: exchange,
    )
    with pytest.raises(CcxtReadOnlyAccountError, match="invalid free balance"):
        provider.fetch_balances()


def test_ccxt_readonly_adapter_sanitizes_exchange_errors():
    exchange = FakeExchange(error=RuntimeError("secret=do-not-leak"))
    provider = CcxtReadOnlyAccount(
        "kraken", "key", "secret", exchange_factory=lambda _: exchange,
    )
    with pytest.raises(CcxtReadOnlyAccountError) as error:
        provider.fetch_balances()
    assert "do-not-leak" not in str(error.value)
    assert "RuntimeError" in str(error.value)

import pytest

from app.accounts.binance_spot import AccountConnectorError, BinanceSpotReadOnlyAccount


class FakeBinanceExchange:
    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
        self.calls = []

    def fetch_balance(self):
        self.calls.append("fetch_balance")
        if self.error:
            raise self.error
        return self.response


def test_binance_connector_initializes_spot_with_rate_limiting_and_reads_balances_only():
    exchange = FakeBinanceExchange({
        "free": {"BTC": "0.1", "EUR": "20", "ZERO": "0"},
        "used": {"BTC": "0.02", "EUR": "0", "ZERO": "0"},
        "total": {"BTC": "0.12", "EUR": "20", "ZERO": "0"},
    })
    config = {}

    def factory(options):
        config.update(options)
        return exchange

    provider = BinanceSpotReadOnlyAccount(
        "read-only-key", "read-only-secret",
        exchange_factory=factory,
    )

    result = provider.fetch_balances()
    description = provider.describe()

    assert config["apiKey"] == "read-only-key"
    assert config["secret"] == "read-only-secret"
    assert config["enableRateLimit"] is True
    assert config["timeout"] == 10000
    assert config["options"]["defaultType"] == "spot"
    assert exchange.calls == ["fetch_balance"]
    assert result["read_only"] is True
    assert result["execution_enabled"] is False
    assert result["withdrawals_enabled"] is False
    assert result["balance_count"] == 2
    assert result["balances"] == [
        {"asset": "BTC", "free": 0.1, "used": 0.02, "total": 0.12},
        {"asset": "EUR", "free": 20.0, "used": 0.0, "total": 20.0},
    ]
    assert description["order_submission"] is False
    assert description["order_management"] is False
    assert description["withdrawals"] is False
    assert description["account_key_permissions_verified"] is False


def test_binance_connector_rejects_missing_credentials():
    with pytest.raises(AccountConnectorError, match="API key"):
        BinanceSpotReadOnlyAccount("", "secret", exchange_factory=lambda _: FakeBinanceExchange())
    with pytest.raises(AccountConnectorError, match="API secret"):
        BinanceSpotReadOnlyAccount("key", "", exchange_factory=lambda _: FakeBinanceExchange())


@pytest.mark.parametrize("value", ["not-a-number", float("inf"), float("nan"), -0.1, True])
def test_binance_connector_rejects_malformed_or_unsafe_balances(value):
    exchange = FakeBinanceExchange({"free": {"BTC": value}, "used": {}, "total": {}})
    provider = BinanceSpotReadOnlyAccount(
        "key", "secret", exchange_factory=lambda _: exchange,
    )
    with pytest.raises(AccountConnectorError, match="invalid|malformed"):
        provider.fetch_balances()


def test_binance_connector_rejects_malformed_balance_collections():
    exchange = FakeBinanceExchange({"free": [], "used": {}, "total": {}})
    provider = BinanceSpotReadOnlyAccount(
        "key", "secret", exchange_factory=lambda _: exchange,
    )
    with pytest.raises(AccountConnectorError, match="malformed balance collections"):
        provider.fetch_balances()


def test_binance_connector_does_not_leak_provider_exception_details():
    secret = "sensitive-secret-value"
    exchange = FakeBinanceExchange(error=RuntimeError(f"request failed apiSecret={secret}"))
    provider = BinanceSpotReadOnlyAccount(
        "key", secret, exchange_factory=lambda _: exchange,
    )

    with pytest.raises(AccountConnectorError) as error:
        provider.fetch_balances()

    assert secret not in str(error.value)
    assert "apiSecret" not in str(error.value)


def test_binance_connector_rejects_invalid_asset_codes():
    exchange = FakeBinanceExchange({"free": {"BTC/USDT": 1}, "used": {}, "total": {}})
    provider = BinanceSpotReadOnlyAccount(
        "key", "secret", exchange_factory=lambda _: exchange,
    )
    with pytest.raises(AccountConnectorError, match="invalid asset code"):
        provider.fetch_balances()


def test_binance_credentials_are_secret_settings_and_never_appear_in_settings_repr():
    from app.config import Settings

    api_key = "example-read-only-key"
    api_secret = "example-read-only-secret"
    config = Settings(
        _env_file=None,
        binance_readonly_api_key=api_key,
        binance_readonly_api_secret=api_secret,
    )

    assert config.binance_readonly_api_key.get_secret_value() == api_key
    assert config.binance_readonly_api_secret.get_secret_value() == api_secret
    assert api_key not in repr(config)
    assert api_secret not in repr(config)

from datetime import datetime, timezone

import pytest

from app.accounts.contracts import (
    AccountProviderError,
    AccountProviderRegistry,
    UnsupportedAccountCapability,
)


class FakePositionProvider:
    provider_id = "positions:test"

    def __init__(self, positions=None, *, declares_positions=True):
        self.positions = positions or [{
            "instrument_id": "BTC/USDT",
            "symbol": "BTC/USDT",
            "asset_class": "crypto",
            "market_type": "spot",
            "quantity": "0.125000",
            "side": "long",
            "quote_currency": "USDT",
            "average_entry_price": "60000.00",
            "mark_price": "61000",
            "market_value": "7625",
            "unrealized_pnl": "125",
        }]
        self.declares_positions = declares_positions
        self.calls = 0

    def describe(self):
        return {
            "provider_id": self.provider_id,
            "account_alias": "test-account",
            "venue_type": "cex",
            "environment": "production",
            "market_types": ["spot"],
            "configured": True,
            "balances": True,
            "positions": self.declares_positions,
            "read_only_connector": True,
            "order_management": False,
            "order_submission": False,
            "order_cancellation": False,
            "reconciliation": False,
            "transfers": False,
            "withdrawals": False,
            "wallet_signing": False,
            "account_key_permissions_verified": False,
        }

    def fetch_balances(self):
        return {
            "provider_id": self.provider_id,
            "account_alias": "test-account",
            "market_type": "spot",
            "environment": "production",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "execution_enabled": False,
            "withdrawals_enabled": False,
            "balances": [],
        }

    def fetch_positions(self, market_type=None):
        self.calls += 1
        selected_market = market_type or "spot"
        return {
            "provider_id": self.provider_id,
            "account_alias": "test-account",
            "market_type": selected_market,
            "environment": "production",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "positions": [
                item for item in self.positions if item["market_type"] == selected_market
            ],
        }


def test_position_snapshot_is_normalized_and_bound_to_provider_account():
    provider = FakePositionProvider()
    registry = AccountProviderRegistry()
    registry.register(provider)

    result = registry.fetch_positions(provider.provider_id, "test-account")

    assert result["provider_id"] == provider.provider_id
    assert result["account_alias"] == "test-account"
    assert result["environment"] == "production"
    assert result["market_type"] == "spot"
    assert result["position_count"] == 1
    assert result["positions"][0]["quantity"] == "0.125"
    assert result["positions"][0]["average_entry_price"] == "60000"
    assert result["positions"][0]["unrealized_pnl"] == "125"
    assert provider.calls == 1


def test_provider_without_declared_position_capability_is_rejected_before_fetch():
    provider = FakePositionProvider(declares_positions=False)
    registry = AccountProviderRegistry()
    registry.register(provider)

    with pytest.raises(UnsupportedAccountCapability):
        registry.fetch_positions(provider.provider_id, "test-account")
    assert provider.calls == 0


@pytest.mark.parametrize("quantity", ["0", "-1", "1e3", "NaN", "Infinity", "1,5", 1.5, True, None])
def test_invalid_position_quantities_are_rejected(quantity):
    provider = FakePositionProvider(positions=[{
        "instrument_id": "BTC/USDT",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "market_type": "spot",
        "quantity": quantity,
        "side": "long",
        "quote_currency": "USDT",
    }])
    registry = AccountProviderRegistry()
    registry.register(provider)

    with pytest.raises(AccountProviderError):
        registry.fetch_positions(provider.provider_id, "test-account")


def test_position_snapshot_rejects_duplicate_instrument_side():
    position = {
        "instrument_id": "BTC/USDT",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "market_type": "spot",
        "quantity": "1",
        "side": "long",
        "quote_currency": "USDT",
    }
    provider = FakePositionProvider(positions=[position, dict(position)])
    registry = AccountProviderRegistry()
    registry.register(provider)

    with pytest.raises(AccountProviderError, match="duplicate.*positions"):
        registry.fetch_positions(provider.provider_id, "test-account")


def test_position_snapshot_rejects_market_type_not_declared_by_provider():
    provider = FakePositionProvider(positions=[{
        "instrument_id": "BTC/USDT",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "market_type": "future",
        "quantity": "1",
        "side": "long",
        "quote_currency": "USDT",
    }])
    registry = AccountProviderRegistry()
    registry.register(provider)

    with pytest.raises(AccountProviderError, match="market_type is not declared"):
        registry.fetch_positions(provider.provider_id, "test-account", market_type="future")


def test_position_snapshot_rejects_unsupported_symbols_and_non_decimal_prices():
    provider = FakePositionProvider(positions=[{
        "instrument_id": "../../BTC",
        "symbol": "BTC/USDT",
        "asset_class": "crypto",
        "market_type": "spot",
        "quantity": "1",
        "side": "long",
        "quote_currency": "USDT",
        "mark_price": "1e6",
    }])
    registry = AccountProviderRegistry()
    registry.register(provider)

    with pytest.raises(AccountProviderError):
        registry.fetch_positions(provider.provider_id, "test-account")

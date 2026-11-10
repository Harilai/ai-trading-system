from datetime import datetime, timedelta, timezone

import pytest

from app.accounts.contracts import (
    AccountBalance,
    AccountCapabilities,
    AmbiguousAccountProvider,
    AccountProviderError,
    AccountProviderNotFound,
    AccountProviderRegistry,
    UnsupportedAccountCapability,
)


class FakeAccountProvider:
    def __init__(
        self,
        *,
        provider_id="test:venue",
        account_alias="account-1",
        read_only=True,
        order_submission=False,
        balances=None,
    ):
        self._description = {
            "provider_id": provider_id,
            "account_alias": account_alias,
            "venue_type": "broker",
            "market_types": ["spot"],
            "environment": "demo",
            "configured": True,
            "balances": True,
            "positions": False,
            "order_management": False,
            "order_submission": order_submission,
            "withdrawals": False,
            "transfers": False,
            "wallet_signing": False,
            "read_only_connector": read_only,
            "account_key_permissions_verified": False,
        }
        self._balances = balances or [{"asset": "BTC", "free": "0.1000", "used": "0", "total": "0.1000"}]

    def describe(self):
        return dict(self._description)

    def fetch_balances(self):
        return {
            "provider_id": self._description["provider_id"],
            "account_alias": self._description["account_alias"],
            "market_type": "spot",
            "environment": "demo",
            "read_only": self._description["read_only_connector"],
            "execution_enabled": self._description["order_submission"],
            "withdrawals_enabled": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "balances": list(self._balances),
        }


def test_account_capabilities_are_normalized_without_inventing_permissions():
    provider = FakeAccountProvider()
    capabilities = AccountCapabilities.from_description(provider.describe())

    assert capabilities.provider_id == "test:venue"
    assert capabilities.venue_type == "broker"
    assert capabilities.environment == "demo"
    assert capabilities.read_only is True
    assert capabilities.order_submission is False
    assert capabilities.withdrawals is False
    assert capabilities.transfers is False
    assert capabilities.wallet_signing is False
    assert capabilities.permissions_verified is False
    assert capabilities.positions is False


def test_account_capabilities_reject_conflicting_read_only_and_write_permissions():
    provider = FakeAccountProvider(read_only=True, order_submission=True)
    with pytest.raises(AccountProviderError, match="conflicts"):
        AccountCapabilities.from_description(provider.describe())


def test_account_balance_normalizes_plain_decimal_strings_and_rejects_floats():
    balance = AccountBalance.normalize({
        "asset": "btc", "free": "0.1000", "used": "0.000", "total": None,
    })
    assert balance.asset == "BTC"
    assert balance.free == "0.1"
    assert balance.used == "0"
    assert balance.total == "0.1"

    with pytest.raises(AccountProviderError, match="plain decimal string"):
        AccountBalance.normalize({
            "asset": "BTC", "free": 0.1, "used": "0", "total": "0.1",
        })


def test_registry_registers_accounts_and_normalizes_balance_snapshots():
    registry = AccountProviderRegistry()
    registry.register(FakeAccountProvider())

    accounts = registry.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["provider_id"] == "test:venue"
    snapshot = registry.fetch_balances("test:venue")

    assert snapshot["provider_id"] == "test:venue"
    assert snapshot["environment"] == "demo"
    assert snapshot["read_only"] is True
    assert snapshot["execution_enabled"] is False
    assert snapshot["transfers_enabled"] is False
    assert snapshot["balances"] == [
        {"asset": "BTC", "free": "0.1", "used": "0", "total": "0.1"}
    ]


def test_registry_rejects_duplicate_accounts_and_unknown_providers():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider()
    registry.register(provider)
    with pytest.raises(AccountProviderError, match="already registered"):
        registry.register(provider)
    with pytest.raises(AccountProviderNotFound):
        registry.fetch_balances("missing:venue")


def test_registry_rejects_provider_identity_and_permission_mismatches():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider()
    registry.register(provider)
    provider.fetch_balances = lambda: {
        **FakeAccountProvider().fetch_balances(),
        "provider_id": "other:venue",
    }
    with pytest.raises(AccountProviderError, match="identity changed"):
        registry.fetch_balances("test:venue")


def test_registry_rejects_duplicate_or_malformed_asset_balances():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider(balances=[
        {"asset": "BTC", "free": "1", "used": "0", "total": "1"},
        {"asset": "btc", "free": "2", "used": "0", "total": "2"},
    ])
    registry.register(provider)
    with pytest.raises(AccountProviderError, match="duplicate asset"):
        registry.fetch_balances("test:venue")


def test_registry_only_records_explicitly_read_only_spot_accounts():
    registry = AccountProviderRegistry()
    registry.register(FakeAccountProvider())
    class SnapshotStore:
        def record_snapshot(self, snapshot):
            return {"provider_id": snapshot["provider_id"], "stored": True}
    result = registry.record_read_only_snapshot("test:venue", SnapshotStore())
    assert result == {"provider_id": "test:venue", "stored": True}

    trading_registry = AccountProviderRegistry()
    trading_registry.register(FakeAccountProvider(
        provider_id="test:demo", read_only=False, order_submission=True,
    ))
    with pytest.raises(UnsupportedAccountCapability, match="read-only"):
        trading_registry.record_read_only_snapshot("test:demo", SnapshotStore())


def test_registry_requires_timezone_aware_fresh_balance_timestamp():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider()
    registry.register(provider)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    provider.fetch_balances = lambda: {
        **FakeAccountProvider().fetch_balances(),
        "fetched_at": stale,
    }
    with pytest.raises(AccountProviderError, match="stale"):
        registry.fetch_balances("test:venue")


def test_registry_rejects_production_demo_environment_mismatch():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider()
    registry.register(provider)
    provider.fetch_balances = lambda: {
        **FakeAccountProvider().fetch_balances(),
        "environment": "production",
    }
    with pytest.raises(AccountProviderError, match="environment"):
        registry.fetch_balances("test:venue")


def test_registry_sanitizes_provider_errors_and_does_not_leak_credentials():
    registry = AccountProviderRegistry()
    provider = FakeAccountProvider()
    registry.register(provider)
    secret = "private-demo-secret"
    def fail():
        raise RuntimeError(f"request failed with secret={secret}")
    provider.fetch_balances = fail

    with pytest.raises(AccountProviderError) as error:
        registry.fetch_balances("test:venue")

    assert secret not in str(error.value)
    assert "request failed with secret" not in str(error.value)


def test_registry_requires_alias_when_multiple_accounts_share_provider_id():
    registry = AccountProviderRegistry()
    registry.register(FakeAccountProvider(account_alias="account-one"))
    registry.register(FakeAccountProvider(account_alias="account-two"))

    with pytest.raises(AmbiguousAccountProvider, match="specify account_alias"):
        registry.fetch_balances("test:venue")
    assert registry.fetch_balances("test:venue", "account-two")["account_alias"] == "account-two"


def test_registry_supports_position_only_readonly_accounts_and_combined_snapshots():
    class FakePositionProvider:
        def describe(self):
            return {
                "provider_id": "test:equity-broker",
                "account_alias": "cash-equities",
                "venue_type": "broker",
                "market_types": ["cash_equity"],
                "environment": "production",
                "configured": True,
                "balances": False,
                "positions": True,
                "order_management": False,
                "order_submission": False,
                "order_cancellation": False,
                "reconciliation": False,
                "read_only_connector": True,
                "transfers": False,
                "withdrawals": False,
                "wallet_signing": False,
                "account_key_permissions_verified": False,
            }

        def fetch_positions(self):
            return {
                "provider_id": "test:equity-broker",
                "account_alias": "cash-equities",
                "market_type": "cash_equity",
                "environment": "production",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "positions": [{
                    "instrument_id": "broker:equity:AAPL",
                    "symbol": "AAPL",
                    "asset_class": "equity",
                    "market_type": "cash_equity",
                    "quantity": "2.000",
                    "side": "long",
                    "quote_currency": "USD",
                    "average_entry_price": "180",
                    "mark_price": "185",
                    "market_value": "370",
                    "unrealized_pnl": "-10.50",
                }],
            }

    registry = AccountProviderRegistry()
    registry.register(FakePositionProvider())
    positions = registry.fetch_positions("test:equity-broker")
    assert positions[0]["quantity"] == "2"
    assert positions[0]["unrealized_pnl"] == "-10.5"

    class SnapshotStore:
        def record_snapshot(self, snapshot):
            return snapshot

    saved = registry.record_read_only_snapshot(
        "test:equity-broker", SnapshotStore(),
    )
    assert saved["market_type"] == "cash_equity"
    assert saved["balances"] == []
    assert saved["positions"][0]["symbol"] == "AAPL"


def test_registry_rejects_position_provider_that_does_not_implement_declared_method():
    class BrokenPositionProvider:
        def describe(self):
            return {
                "provider_id": "test:broken-position",
                "account_alias": "account",
                "venue_type": "broker",
                "market_types": ["cash_equity"],
                "environment": "production",
                "configured": True,
                "balances": False,
                "positions": True,
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

    with pytest.raises(AccountProviderError, match="fetch_positions"):
        AccountProviderRegistry().register(BrokenPositionProvider())


def test_registry_rejects_duplicate_or_wrong_market_positions():
    class FakePositionProvider:
        def __init__(self, positions):
            self.positions = positions

        def describe(self):
            return {
                "provider_id": "test:positions",
                "account_alias": "one",
                "venue_type": "broker",
                "market_types": ["cash_equity"],
                "environment": "production",
                "configured": True,
                "balances": False,
                "positions": True,
                "order_management": False,
                "order_submission": False,
                "order_cancellation": False,
                "reconciliation": False,
                "read_only_connector": True,
                "transfers": False,
                "withdrawals": False,
                "wallet_signing": False,
                "account_key_permissions_verified": False,
            }

        def fetch_positions(self):
            return {
                "provider_id": "test:positions",
                "account_alias": "one",
                "market_type": "cash_equity",
                "environment": "production",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "positions": self.positions,
            }

    base = {
        "instrument_id": "broker:AAPL", "symbol": "AAPL", "asset_class": "equity",
        "market_type": "cash_equity", "quantity": "1", "side": "long",
        "quote_currency": "USD",
    }
    registry = AccountProviderRegistry()
    registry.register(FakePositionProvider([base, base]))
    with pytest.raises(AccountProviderError, match="duplicate"):
        registry.fetch_positions("test:positions")

    wrong_market_registry = AccountProviderRegistry()
    wrong_market_registry.register(FakePositionProvider([{**base, "market_type": "spot"}]))
    with pytest.raises(AccountProviderError, match="market_type"):
        wrong_market_registry.fetch_positions("test:positions")

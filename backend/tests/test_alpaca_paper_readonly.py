import httpx
import pytest

from app.accounts.alpaca_paper_readonly import (
    AlpacaPaperAccountError,
    AlpacaPaperReadOnlyAccount,
    PAPER_API_BASE_URL,
)
from app.accounts.contracts import AccountProviderRegistry


ACCOUNT = {"id": "paper-account-123"}
POSITION = {
    "asset_id": "asset-uuid-123",
    "symbol": "AAPL",
    "asset_class": "us_equity",
    "qty": "2.5000",
    "side": "long",
    "avg_entry_price": "180.1200",
    "current_price": "185.50",
    "market_value": "463.75",
    "unrealized_pl": "13.45",
}


def make_provider(positions=None, *, account=ACCOUNT, status=200, expected_account_id="paper-account-123"):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v2/account":
            return httpx.Response(200, json=account)
        if request.url.path == "/v2/positions":
            return httpx.Response(status, json=positions if positions is not None else [])
        return httpx.Response(404)

    client = httpx.Client(
        base_url=PAPER_API_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    provider = AlpacaPaperReadOnlyAccount(
        "test-key", "test-secret", client=client,
        expected_account_id=expected_account_id,
    )
    return provider, requests


def test_alpaca_paper_positions_normalize_with_asset_id_and_decimal_strings():
    provider, requests = make_provider([POSITION])

    snapshot = provider.fetch_positions_snapshot(market_type="cash")

    assert snapshot["provider_id"] == "alpaca:paper"
    assert snapshot["environment"] == "paper"
    assert snapshot["coverage_status"] == "complete"
    assert snapshot["positions"] == [{
        "instrument_id": "asset-uuid-123",
        "symbol": "AAPL",
        "asset_class": "equity",
        "market_type": "cash",
        "quantity": "2.5",
        "side": "long",
        "quote_currency": "USD",
        "average_entry_price": "180.12",
        "mark_price": "185.5",
        "market_value": "463.75",
        "unrealized_pnl": "13.45",
    }]
    assert [request.method for request in requests] == ["GET", "GET"]
    assert all(request.url.host == "paper-api.alpaca.markets" for request in requests)
    assert [request.url.path for request in requests] == ["/v2/account", "/v2/positions"]


def test_empty_positions_are_complete_only_after_account_identity_check():
    provider, requests = make_provider([])

    result = provider.fetch_positions_snapshot(market_type="cash")

    assert result["coverage_status"] == "complete"
    assert result["positions"] == []
    assert [request.url.path for request in requests] == ["/v2/account", "/v2/positions"]


def test_expected_account_id_mismatch_fails_closed_before_positions_request():
    provider, requests = make_provider([], expected_account_id="another-account")

    with pytest.raises(AlpacaPaperAccountError, match="identity"):
        provider.fetch_positions_snapshot(market_type="cash")
    assert [request.url.path for request in requests] == ["/v2/account"]


@pytest.mark.parametrize("account", [None, [], {}, {"id": ""}, {"id": 123}])
def test_invalid_account_identity_is_not_treated_as_empty_positions(account):
    provider, requests = make_provider([], account=account)

    with pytest.raises(AlpacaPaperAccountError):
        provider.fetch_positions_snapshot(market_type="cash")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_positions_http_errors_fail_closed(status):
    provider, _ = make_provider([], status=status)

    with pytest.raises(AlpacaPaperAccountError, match=f"HTTP {status}"):
        provider.fetch_positions_snapshot(market_type="cash")


@pytest.mark.parametrize("quantity", ["0", "-1", "1e3", "NaN", "Infinity", 1.5, True, None])
def test_malformed_position_quantity_is_rejected(quantity):
    provider, _ = make_provider([{**POSITION, "qty": quantity}])

    with pytest.raises(AlpacaPaperAccountError):
        provider.fetch_positions_snapshot(market_type="cash")


def test_duplicate_asset_ids_are_rejected():
    provider, _ = make_provider([POSITION, dict(POSITION)])

    with pytest.raises(AlpacaPaperAccountError, match="duplicate"):
        provider.fetch_positions_snapshot(market_type="cash")


def test_unknown_asset_class_is_rejected_instead_of_guessed():
    provider, _ = make_provider([{**POSITION, "asset_class": "crypto"}])

    with pytest.raises(AlpacaPaperAccountError, match="unsupported asset class"):
        provider.fetch_positions_snapshot(market_type="cash")


def test_invalid_json_shape_is_rejected():
    provider, _ = make_provider({"not": "a list"})

    with pytest.raises(AlpacaPaperAccountError, match="must be a list"):
        provider.fetch_positions_snapshot(market_type="cash")


def test_provider_is_registered_as_read_only_cash_positions_only():
    provider, _ = make_provider([])
    registry = AccountProviderRegistry()

    capabilities = registry.register(provider)
    positions = registry.fetch_positions(provider.provider_id, provider.account_alias, market_type="cash")
    snapshot = registry.fetch_positions_snapshot(
        provider.provider_id, provider.account_alias, market_type="cash",
    )

    assert positions["positions"] == []
    assert positions["coverage_status"] == "unknown"
    assert capabilities.positions is True
    assert capabilities.balances is False
    assert capabilities.read_only is True
    assert capabilities.order_submission is False
    assert capabilities.transfers is False
    assert capabilities.withdrawals is False
    assert capabilities.wallet_signing is False
    assert snapshot["coverage_status"] == "complete"
    assert snapshot["market_type"] == "cash"


def test_only_cash_market_is_supported():
    provider, requests = make_provider([])

    with pytest.raises(AlpacaPaperAccountError, match="cash"):
        provider.fetch_positions_snapshot(market_type="spot")
    assert requests == []


def test_errors_do_not_include_credentials():
    provider, _ = make_provider([], status=401)

    with pytest.raises(AlpacaPaperAccountError) as error:
        provider.fetch_positions_snapshot(market_type="cash")

    assert "test-key" not in str(error.value)
    assert "test-secret" not in str(error.value)



def test_injected_client_cannot_override_fixed_paper_host():
    client = httpx.Client(base_url="https://api.alpaca.markets")

    with pytest.raises(AlpacaPaperAccountError, match="fixed Paper API host"):
        AlpacaPaperReadOnlyAccount(
            "test-key", "test-secret", expected_account_id="paper-account-123", client=client,
        )
    client.close()

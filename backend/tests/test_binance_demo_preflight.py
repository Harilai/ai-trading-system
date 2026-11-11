from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from app.binance_demo_preflight import (
    PreflightError,
    _validate_api_url,
    run_preflight,
    validate_preflight,
)


def _valid_payloads():
    return (
        {
            "accounts": [{
                "provider_id": "ccxt:binance-demo",
                "account_alias": "binance-spot-demo",
                "environment": "demo",
                "demo_mode": True,
                "demo_only": True,
                "withdrawals": False,
                "transfers": False,
            }]
        },
        {
            "environment": "demo",
            "demo_only": True,
            "withdrawals_enabled": False,
            "balances": [{"asset": "USDT", "total": "1000"}],
        },
        {
            "environment": "demo",
            "demo_only": True,
            "markets": [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}],
        },
    )


def test_preflight_accepts_only_explicit_demo_identity_and_is_read_only():
    result = validate_preflight(*_valid_payloads())
    assert result["environment"] == "demo"
    assert result["active_spot_markets"] == 2
    assert result["order_submission_performed"] is False
    assert result["exchange_mutations_performed"] is False


@pytest.mark.parametrize(
    ("index", "change"),
    [
        (0, lambda payload: payload["accounts"][0].update(environment="production")),
        (0, lambda payload: payload["accounts"][0].update(demo_only=False)),
        (0, lambda payload: payload["accounts"][0].update(withdrawals=True)),
        (1, lambda payload: payload.update(environment="production")),
        (1, lambda payload: payload.update(demo_only=False)),
        (1, lambda payload: payload.update(withdrawals_enabled=True)),
        (2, lambda payload: payload.update(environment="production")),
        (2, lambda payload: payload.update(markets=[])),
    ],
)
def test_preflight_rejects_missing_or_unsafe_demo_evidence(index, change):
    payloads = list(_valid_payloads())
    change(payloads[index])
    with pytest.raises(PreflightError):
        validate_preflight(*payloads)


def test_preflight_rejects_duplicate_market_symbols():
    accounts, balances, markets = _valid_payloads()
    markets["markets"].append({"symbol": "BTC/USDT"})
    with pytest.raises(PreflightError, match="duplicate"):
        validate_preflight(accounts, balances, markets)


def test_preflight_refuses_remote_http_and_embedded_credentials():
    with pytest.raises(PreflightError, match="allow-remote"):
        _validate_api_url("http://example.com")
    with pytest.raises(PreflightError, match="HTTPS"):
        _validate_api_url("http://example.com", allow_remote=True)
    with pytest.raises(PreflightError, match="credentials"):
        _validate_api_url("http://user:pass@127.0.0.1:8000")


def test_preflight_fetches_only_three_get_endpoints_and_never_mutates():
    accounts, balances, markets = _valid_payloads()
    payloads = [accounts, balances, markets]
    seen = []

    def fake_opener(request, timeout):
        seen.append((request.full_url, request.get_method(), request.get_header("X-agent-token"), timeout))
        return BytesIO(json.dumps(payloads[len(seen) - 1]).encode("utf-8"))

    result = run_preflight("http://127.0.0.1:8000", "owner-token", opener=fake_opener)
    assert [item[1] for item in seen] == ["GET", "GET", "GET"]
    assert all(item[2] == "owner-token" for item in seen)
    assert result["exchange_mutations_performed"] is False


def test_preflight_does_not_print_or_return_owner_token():
    accounts, balances, markets = _valid_payloads()
    result = validate_preflight(accounts, balances, markets)
    assert "owner-token" not in repr(result)


def test_preflight_rejects_http_errors_without_echoing_response_body():
    def fake_opener(request, timeout):
        raise HTTPError(request.full_url, 401, "unauthorized", {}, BytesIO(b"secret response"))

    with pytest.raises(PreflightError, match="HTTP 401") as error:
        run_preflight("http://127.0.0.1:8000", "owner-token", opener=fake_opener)
    assert "secret response" not in str(error.value)

from datetime import datetime, timezone
import sqlite3

import httpx
import pytest

from app.accounts.alpaca_paper_readonly import (
    AlpacaPaperAccountError, AlpacaPaperReadOnlyAccount, PAPER_API_BASE_URL,
)
from app.accounts.financial_metrics_store import (
    AccountFinancialMetricsStore, FinancialMetricsError, normalize_financial_metrics,
)


def account_payload(**overrides):
    payload = {
        "id": "paper-account-123",
        "currency": "USD",
        "status": "ACTIVE",
        "cash": "1000.00",
        "equity": "1250.50",
        "buying_power": "2000.00",
        "portfolio_value": "1250.50",
        "regt_buying_power": "2000",
        "non_marginable_buying_power": "1000",
        "options_buying_power": "0",
        "long_market_value": "250.50",
        "short_market_value": "0",
        "initial_margin": "0",
        "maintenance_margin": "0",
        "last_equity": "1200",
        "last_cash": "1000",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
    }
    payload.update(overrides)
    return payload


def make_provider(payload=None, *, status=200):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload if payload is not None else account_payload())

    client = httpx.Client(
        base_url=PAPER_API_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    provider = AlpacaPaperReadOnlyAccount(
        "test-key", "test-secret", expected_account_id="paper-account-123", client=client,
    )
    return provider, requests


def test_financial_metrics_use_one_get_and_preserve_distinct_semantics():
    provider, requests = make_provider()
    result = provider.fetch_financial_metrics_snapshot()

    assert [request.method for request in requests] == ["GET"]
    assert [request.url.path for request in requests] == ["/v2/account"]
    assert result["cash"] == "1000"
    assert result["equity"] == "1250.5"
    assert result["buying_power"] == "2000"
    assert result["portfolio_value"] == "1250.5"
    assert result["provider_id"] == "alpaca:paper"
    assert result["environment"] == "paper"
    assert result["account_id"] == "paper-account-123"


def test_metrics_fail_closed_on_identity_currency_or_account_status():
    for payload in (
        account_payload(id="another-account"),
        account_payload(currency=None),
        account_payload(status="ACCOUNT_UPDATED"),
    ):
        provider, requests = make_provider(payload)
        with pytest.raises(AlpacaPaperAccountError):
            provider.fetch_financial_metrics_snapshot()
        assert len(requests) == 1


@pytest.mark.parametrize("value", [1.5, True, "1e3", "NaN", "Infinity", "-1"])
def test_invalid_unsigned_financial_metric_is_rejected(value):
    provider, _ = make_provider(account_payload(cash=value))
    with pytest.raises(AlpacaPaperAccountError):
        provider.fetch_financial_metrics_snapshot()


def test_signed_equity_is_preserved_but_optional_missing_fields_remain_null():
    payload = account_payload(equity="-12.50")
    payload.pop("portfolio_value")
    payload.pop("pending_transfer_in", None)
    provider, _ = make_provider(payload)
    result = provider.fetch_financial_metrics_snapshot()
    assert result["equity"] == "-12.5"
    assert result["portfolio_value"] is None
    assert result["pending_transfer_in"] is None


def valid_snapshot(**overrides):
    result = {
        "provider_id": "alpaca:paper",
        "account_alias": "alpaca-paper-readonly",
        "environment": "paper",
        "account_id": "paper-account-123",
        "currency": "USD",
        "account_status": "ACTIVE",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cash": "1000",
        "equity": "1250.50",
        "buying_power": "2000",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
    }
    result.update(overrides)
    return result


def test_normalization_keeps_cash_equity_and_buying_power_separate():
    normalized = normalize_financial_metrics(valid_snapshot())
    assert normalized["metrics"]["cash"] == "1000"
    assert normalized["metrics"]["equity"] == "1250.5"
    assert normalized["metrics"]["buying_power"] == "2000"
    assert normalized["coverage_status"] == "complete"
    assert normalized["schema_version"] == 1


@pytest.mark.parametrize("value", [1.5, True, "1e3", "NaN", "Infinity", "-1"])
def test_store_rejects_invalid_unsigned_metric_types_and_values(value):
    with pytest.raises(FinancialMetricsError):
        normalize_financial_metrics(valid_snapshot(cash=value))


def test_store_is_append_only_and_idempotent(tmp_path):
    store = AccountFinancialMetricsStore(str(tmp_path / "metrics.db"))
    snapshot = valid_snapshot()
    first = store.record_snapshot("request-1", snapshot)
    replay = store.record_snapshot("request-1", snapshot)
    assert replay["snapshot_id"] == first["snapshot_id"]
    assert len(store.list_snapshots()) == 1

    with pytest.raises(FinancialMetricsError, match="different payload"):
        store.record_snapshot("request-1", valid_snapshot(cash="999"))

    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE account_financial_metric_snapshots SET currency='EUR' WHERE snapshot_id=?",
                (first["snapshot_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "DELETE FROM account_financial_metric_snapshots WHERE snapshot_id=?",
                (first["snapshot_id"],),
            )


def test_store_rejects_non_paper_or_inactive_account_and_missing_required_fields():
    with pytest.raises(FinancialMetricsError, match="Paper"):
        normalize_financial_metrics(valid_snapshot(environment="production"))
    with pytest.raises(FinancialMetricsError, match="ACTIVE"):
        normalize_financial_metrics(valid_snapshot(account_status="ACCOUNT_UPDATED"))
    with pytest.raises(FinancialMetricsError, match="Required"):
        normalize_financial_metrics(valid_snapshot(buying_power=None))


def test_optional_metrics_are_null_not_synthetic_zero(tmp_path):
    snapshot = valid_snapshot()
    store = AccountFinancialMetricsStore(str(tmp_path / "metrics.db"))
    result = store.record_snapshot("request-optional", snapshot)
    assert result["metrics"]["portfolio_value"] is None
    assert result["metrics"]["cash_transferable"] is None

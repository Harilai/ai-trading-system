"""Read-only ingestion of provider order evidence into the common ledger."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import AccountFillHistory, AccountOrder, AccountProviderError
from .order_ledger import AccountOrderLedger, AccountOrderLedgerError


class AccountOrderReconciliationError(RuntimeError):
    pass


class AccountOrderReconciliationService:
    """Refresh one known order from a provider and persist only verified evidence.

    The service never submits, cancels or retries orders. A missing order or
    uncertain identity leaves the existing ledger state untouched.
    """

    def __init__(self, provider: Any, ledger: AccountOrderLedger):
        self.provider = provider
        self.ledger = ledger

    @staticmethod
    def _matches_scope(observed: AccountOrder, saved: dict) -> bool:
        return all(getattr(observed, field) == saved[field] for field in (
            "provider_id", "account_alias", "environment", "market_type",
            "instrument_id", "symbol", "side", "order_type", "requested_quantity",
            "quote_currency",
        ))

    def reconcile_order(self, order_key: str) -> dict:
        saved = self.ledger.get_order(order_key)
        if saved is None:
            raise AccountOrderReconciliationError("Order does not exist in the normalized ledger")
        if not saved["order_id"]:
            return {
                "order_key": order_key,
                "status": saved["status"],
                "updated": False,
                "reason": "exchange_order_id_missing",
                "fills": None,
            }

        raw_order = self.provider.fetch_order(saved["order_id"], saved["instrument_id"])
        if raw_order is None:
            return {
                "order_key": order_key,
                "status": saved["status"],
                "updated": False,
                "reason": "provider_order_not_found",
                "fills": None,
            }
        try:
            observed = AccountOrder.normalize(raw_order)
        except AccountProviderError as exc:
            raise AccountOrderReconciliationError(str(exc)) from None
        if not self._matches_scope(observed, saved):
            raise AccountOrderReconciliationError("Provider order identity or immutable terms conflict with the ledger")
        if observed.order_id is not None and observed.order_id != saved["order_id"]:
            raise AccountOrderReconciliationError("Provider returned a different exchange order ID")
        if (observed.client_order_id is not None and saved["client_order_id"] is not None
                and observed.client_order_id != saved["client_order_id"]):
            raise AccountOrderReconciliationError("Provider returned a different client order ID")

        try:
            recorded = self.ledger.record_order(observed)
        except AccountOrderLedgerError as exc:
            raise AccountOrderReconciliationError(str(exc)) from None

        fill_summary = None
        if Decimal(observed.filled_quantity) > 0:
            exchange_order_id = observed.order_id or saved["order_id"]
            raw_history = self.provider.fetch_fills(exchange_order_id, observed.instrument_id)
            try:
                history = AccountFillHistory.normalize(raw_history)
            except AccountProviderError as exc:
                raise AccountOrderReconciliationError(str(exc)) from None
            if (history.provider_id, history.account_alias, history.environment,
                    history.market_type, history.instrument_id) != (
                saved["provider_id"], saved["account_alias"], saved["environment"],
                    saved["market_type"], saved["instrument_id"],
            ):
                raise AccountOrderReconciliationError("Provider fill history belongs to a different account or instrument")
            if history.order_id is not None and history.order_id != exchange_order_id:
                raise AccountOrderReconciliationError("Provider fill history belongs to a different exchange order")
            if (history.client_order_id is not None and saved["client_order_id"] is not None
                    and history.client_order_id != saved["client_order_id"]):
                raise AccountOrderReconciliationError("Provider fill history belongs to a different client order")
            try:
                fill_summary = self.ledger.record_fills(
                    order_key,
                    [fill.to_dict() for fill in history.fills],
                    history_complete=history.history_complete,
                )
            except AccountOrderLedgerError as exc:
                raise AccountOrderReconciliationError(str(exc)) from None

        return {
            "order_key": order_key,
            "status": observed.status,
            "updated": not recorded["idempotent_replay"],
            "fills": fill_summary,
        }


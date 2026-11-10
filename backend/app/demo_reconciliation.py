from __future__ import annotations

from decimal import Decimal
from typing import Any

from .accounts.binance_demo import BinanceDemoError
from .accounts.demo_order_store import BinanceDemoOrderStore, DemoOrderStoreError


_ACTIVE = {"intent_created", "submission_unknown", "open", "partially_filled"}
_FILL_STATES = {"partially_filled", "filled", "cancelled", "expired"}


class BinanceDemoReconciler:
    """Leased, bounded polling of persisted Binance Spot Demo orders and fills."""

    def __init__(
        self,
        store: BinanceDemoOrderStore,
        provider: Any,
        *,
        interval_seconds: int = 60,
        batch_size: int = 5,
        lease_seconds: int = 180,
    ):
        if not 30 <= interval_seconds <= 3600:
            raise ValueError("Demo reconciliation interval must be between 30 and 3600 seconds")
        if not 1 <= batch_size <= 100:
            raise ValueError("Demo reconciliation batch size must be between 1 and 100")
        if not 30 <= lease_seconds <= 900:
            raise ValueError("Demo reconciliation lease must be between 30 and 900 seconds")
        self.store = store
        self.provider = provider
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds

    def _apply_order_snapshot(self, order: dict, normalized: dict) -> dict:
        return self.store.transition(
            order["client_order_id"],
            normalized["status"],
            exchange_order_id=normalized.get("exchange_order_id"),
            exchange_status=normalized.get("exchange_status"),
            filled=normalized.get("filled"),
            remaining=normalized.get("remaining"),
            average=normalized.get("average"),
            error=normalized.get("error"),
            payload={
                "source": "periodic_exchange_reconciliation",
                "exchange_order_id": normalized.get("exchange_order_id"),
                "exchange_status": normalized.get("exchange_status"),
                "filled": normalized.get("filled"),
                "remaining": normalized.get("remaining"),
                "average": normalized.get("average"),
                "observed_client_order_id": normalized.get("observed_client_order_id"),
                "observed_exchange_order_id": normalized.get("observed_exchange_order_id"),
                "observed_exchange_status": normalized.get("observed_exchange_status"),
            },
        )

    def _reconcile_fills(self, order: dict) -> dict:
        if not order.get("exchange_order_id"):
            raise DemoOrderStoreError("Cannot reconcile individual fills without a confirmed exchange order ID")
        if Decimal(order.get("filled") or "0") == 0:
            return self.store.record_fills(order["client_order_id"], [])
        history = self.provider.fetch_order_fills(
            symbol=order["symbol"],
            exchange_order_id=order["exchange_order_id"],
            limit=1000,
        )
        result = self.store.record_fills(order["client_order_id"], history["fills"])
        return {
            **result,
            "exchange_history_complete": bool(history["complete"]),
            "exchange_records_returned": int(history["returned_count"]),
            "accounting_complete": bool(result["fill_coverage_complete"]),
        }

    def _reconcile_one(self, order: dict) -> dict:
        client_order_id = order["client_order_id"]
        current = order
        if current["status"] == "intent_created":
            current = self.store.transition(
                client_order_id,
                "submission_unknown",
                error="Periodic recovery found an intent interrupted before submission outcome was recorded",
                payload={"source": "periodic_restart_recovery", "resubmission": False},
            )

        if current["status"] in _ACTIVE:
            if current.get("exchange_order_id"):
                normalized = self.provider.fetch_order_by_id(
                    symbol=current["symbol"],
                    exchange_order_id=current["exchange_order_id"],
                    client_order_id=client_order_id,
                    expected_amount=current["amount"],
                    expected_side=current["side"],
                )
            else:
                normalized = self.provider.fetch_order_by_client_id(
                    symbol=current["symbol"],
                    client_order_id=client_order_id,
                    expected_amount=current["amount"],
                    expected_side=current["side"],
                )

            if normalized is None:
                if current["status"] == "submission_unknown":
                    return {"client_order_id": client_order_id, "status": "submission_unknown", "found": False}
                saved = self.store.transition(
                    client_order_id,
                    "reconciliation_required",
                    error="Previously acknowledged Demo order is missing from exchange lookup",
                    payload={"source": "periodic_exchange_reconciliation", "found": False},
                )
                return {"client_order_id": client_order_id, "status": saved["status"], "found": False}

            current = self._apply_order_snapshot(current, normalized)

        result = {
            "client_order_id": client_order_id,
            "status": current["status"],
            "found": True,
        }
        if current["status"] in _FILL_STATES and Decimal(current.get("filled") or "0") > 0:
            result["fills"] = self._reconcile_fills(current)
        return result

    def run_due(self) -> dict | None:
        lease = self.store.claim_reconciliation_lease(lease_seconds=self.lease_seconds)
        if lease is None:
            return None

        outcomes = []
        failures = []
        try:
            orders = self.store.list_reconcilable_orders(limit=self.batch_size)
            for order in orders:
                try:
                    outcomes.append(self._reconcile_one(order))
                except (BinanceDemoError, DemoOrderStoreError) as exc:
                    awaitable_error = type(exc).__name__
                    self.store.record_reconciliation_failure(
                        order["client_order_id"], awaitable_error,
                    )
                    failures.append({
                        "client_order_id": order["client_order_id"],
                        "error": f"{awaitable_error}: {str(exc)[:300]}",
                    })
                except Exception as exc:
                    self.store.record_reconciliation_failure(
                        order["client_order_id"], type(exc).__name__,
                    )
                    failures.append({
                        "client_order_id": order["client_order_id"],
                        "error": f"Unexpected reconciliation failure ({type(exc).__name__})",
                    })

            result = {
                "processed": len(outcomes) + len(failures),
                "succeeded": len(outcomes),
                "failed": len(failures),
                "outcomes": outcomes,
                "failures": failures,
            }
            if failures:
                result["error"] = f"{len(failures)} Demo order reconciliation(s) failed"
            finished = self.store.finish_reconciliation_lease(
                lease["lease_token"],
                result,
                interval_seconds=self.interval_seconds,
                failed=bool(failures),
            )
            result["lease_completed"] = finished
            return result
        except Exception as exc:
            result = {
                "processed": len(outcomes) + len(failures),
                "succeeded": len(outcomes),
                "failed": len(failures) + 1,
                "outcomes": outcomes,
                "failures": failures,
                "error": f"Reconciliation batch failed ({type(exc).__name__})",
            }
            result["lease_completed"] = self.store.finish_reconciliation_lease(
                lease["lease_token"],
                result,
                interval_seconds=self.interval_seconds,
                failed=True,
            )
            return result

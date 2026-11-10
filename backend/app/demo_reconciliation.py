from __future__ import annotations

from decimal import Decimal
import logging

from .accounts.binance_demo import BinanceDemoError
from .accounts.demo_order_store import (
    BinanceDemoOrderStore,
    DemoOrderConflict,
    DemoOrderStoreError,
)


logger = logging.getLogger("binance-demo-reconciliation")


class BinanceDemoOrderReconciler:
    """Leased, rate-limited reconciliation for Binance Spot Demo orders and fills."""

    def __init__(
        self,
        store: BinanceDemoOrderStore,
        account,
        *,
        interval_seconds: int = 30,
        lease_seconds: int = 120,
        max_orders_per_cycle: int = 3,
    ):
        if not 15 <= interval_seconds <= 3600:
            raise ValueError("Demo reconciliation interval must be between 15 and 3600 seconds")
        if not 30 <= lease_seconds <= 300:
            raise ValueError("Demo reconciliation lease must be between 30 and 300 seconds")
        if not 1 <= max_orders_per_cycle <= 10:
            raise ValueError("Demo reconciliation batch size must be between 1 and 10")
        self.store = store
        self.account = account
        self.interval_seconds = interval_seconds
        self.lease_seconds = lease_seconds
        self.max_orders_per_cycle = max_orders_per_cycle

    def _reconcile_one(self, order: dict, lease_token: str) -> dict:
        client_order_id = order["client_order_id"]
        status = order["status"]
        current = order

        if status in ("intent_created", "submission_unknown", "open", "partially_filled"):
            if status == "intent_created":
                current = self.store.transition(
                    client_order_id,
                    "submission_unknown",
                    error="Recovered interrupted intent; periodic reconciliation never resubmits",
                    payload={"source": "periodic_restart_recovery"},
                    lease_token=lease_token,
                )
            normalized = self.account.fetch_order_by_client_id(
                symbol=current["symbol"],
                client_order_id=client_order_id,
                expected_amount=current["amount"],
                expected_side=current["side"],
            )
            if normalized is None:
                if current["status"] == "submission_unknown":
                    current = self.store.transition(
                        client_order_id,
                        "submission_unknown",
                        error="Demo order is not yet visible by client order ID; no resubmission attempted",
                        payload={"source": "periodic_order_lookup_not_found"},
                        lease_token=lease_token,
                    )
                    return {"client_order_id": client_order_id, "status": current["status"], "fills": None}
                current = self.store.transition(
                    client_order_id,
                    "reconciliation_required",
                    error="Previously acknowledged Demo order is missing from periodic exchange lookup",
                    payload={"source": "periodic_acknowledged_order_missing"},
                    lease_token=lease_token,
                )
                return {"client_order_id": client_order_id, "status": current["status"], "fills": None}

            transition_payload = {
                "source": "periodic_exchange_order_reconciliation",
                "exchange_status": normalized.get("exchange_status"),
                "observed_client_order_id": normalized.get("observed_client_order_id"),
                "observed_exchange_order_id": normalized.get("observed_exchange_order_id"),
            }
            try:
                current = self.store.transition(
                    client_order_id,
                    normalized["status"],
                    exchange_order_id=normalized.get("exchange_order_id"),
                    exchange_status=normalized.get("exchange_status"),
                    filled=normalized.get("filled"),
                    remaining=normalized.get("remaining"),
                    average=normalized.get("average"),
                    error=normalized.get("error"),
                    payload=transition_payload,
                    lease_token=lease_token,
                )
            except DemoOrderConflict:
                raise
            except DemoOrderStoreError as exc:
                # A surprising exchange state must be quarantined, not retried
                # forever as an ordinary worker error.
                current = self.store.transition(
                    client_order_id,
                    "reconciliation_required",
                    error=f"Unexpected exchange order state ({type(exc).__name__})",
                    payload={**transition_payload, "observed_status": normalized.get("status")},
                    lease_token=lease_token,
                )

        fill_summary = None
        filled = Decimal(current.get("filled") or "0")
        if (
            current["status"] in ("partially_filled", "filled", "cancelled", "expired")
            and filled > 0
            and current.get("exchange_order_id")
        ):
            exchange_result = self.account.fetch_order_fills(
                symbol=current["symbol"],
                exchange_order_id=current["exchange_order_id"],
                limit=1000,
            )
            fill_summary = self.store.record_fills(
                client_order_id,
                exchange_result["fills"],
                lease_token=lease_token,
            )
            fill_summary["exchange_history_complete"] = exchange_result["complete"]
            fill_summary["accounting_complete"] = fill_summary["fill_coverage_complete"]

        return {
            "client_order_id": client_order_id,
            "status": current["status"],
            "fills": fill_summary,
        }

    def run_once(self) -> dict:
        lease = self.store.claim_reconciliation_lease(
            interval_seconds=self.interval_seconds,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return {"claimed": False, "processed": 0, "reason": "not_due_or_leased"}

        token = lease["lease_token"]
        try:
            candidates = self.store.list_reconciliation_candidates(
                limit=self.max_orders_per_cycle,
            )
            results = []
            failures = []
            for order in candidates:
                try:
                    results.append(self._reconcile_one(order, token))
                except Exception as exc:
                    failures.append({
                        "client_order_id": order["client_order_id"],
                        "error": str(exc)[:300],
                    })
                    results.append({
                        "client_order_id": order["client_order_id"],
                        "status": order["status"],
                        "failed": True,
                        "error": str(exc)[:300],
                    })
            completed = self.store.complete_reconciliation_lease(
                token,
                success=not failures,
                interval_seconds=self.interval_seconds,
                retry_base_seconds=self.interval_seconds,
            )
            if not completed:
                raise DemoOrderConflict("Demo reconciliation lease expired before cycle completion")
            return {
                "claimed": True,
                "processed": len(results),
                "failed_orders": failures,
                "failed": bool(failures),
                "results": results,
                "next_interval_seconds": self.interval_seconds,
            }
        except (BinanceDemoError, DemoOrderStoreError, OSError, ValueError) as exc:
            self.store.complete_reconciliation_lease(
                token,
                success=False,
                interval_seconds=self.interval_seconds,
                retry_base_seconds=self.interval_seconds,
            )
            logger.warning(
                "Binance Demo reconciliation cycle failed (%s)",
                type(exc).__name__,
            )
            return {
                "claimed": True,
                "processed": 0,
                "failed": True,
                "error": str(exc)[:300],
            }
        except Exception:
            self.store.complete_reconciliation_lease(
                token,
                success=False,
                interval_seconds=self.interval_seconds,
                retry_base_seconds=self.interval_seconds,
            )
            logger.exception("Unexpected Binance Demo reconciliation failure")
            return {
                "claimed": True,
                "processed": 0,
                "failed": True,
                "error": "Unexpected reconciliation failure",
            }

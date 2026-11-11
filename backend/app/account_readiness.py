from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .execution_adapters import PaperPortfolioScopedAdapter, ScopedAccountAdapterError
from .execution_registry import ExecutionEnvironment
from .portfolio import PaperPortfolio, PortfolioError


class AccountReadinessError(RuntimeError):
    """A paper account cannot be considered safe for a new entry."""


class PaperAccountReadiness:
    """Fail-closed preflight for the single internal PAPER ledger.

    This is deliberately not a broker-account verifier. The identity basis is
    the local PaperPortfolio ledger and its explicit internal scope.
    """

    TERMINAL_INTENT_STATES = {"filled", "rejected", "expired"}

    def __init__(
        self,
        portfolio: PaperPortfolio,
        adapter: PaperPortfolioScopedAdapter,
        *,
        max_snapshot_age_seconds: int = 60,
        future_tolerance_seconds: int = 5,
    ) -> None:
        if not isinstance(portfolio, PaperPortfolio):
            raise AccountReadinessError("A PaperPortfolio ledger is required")
        if not isinstance(adapter, PaperPortfolioScopedAdapter) or adapter.portfolio is not portfolio:
            raise AccountReadinessError("The scoped adapter must wrap the same PAPER ledger")
        if max_snapshot_age_seconds <= 0 or future_tolerance_seconds < 0:
            raise AccountReadinessError("Snapshot freshness limits are invalid")
        self.portfolio = portfolio
        self.adapter = adapter
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self.future_tolerance_seconds = future_tolerance_seconds

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AccountReadinessError(f"{field} is invalid") from exc
        if not parsed.is_finite():
            raise AccountReadinessError(f"{field} must be finite")
        return parsed

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise AccountReadinessError("Snapshot timestamp is missing")
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise AccountReadinessError("Snapshot timestamp is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AccountReadinessError("Snapshot timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)

    def evaluate(
        self,
        *,
        side: str = "buy",
        exclude_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        evaluated_at = datetime.now(timezone.utc)
        if side not in {"buy", "sell"}:
            return {
                "status": "blocked",
                "evaluated_at": evaluated_at.isoformat(),
                "blockers": ["unsupported_order_side"],
            }
        if side == "sell":
            return {
                "status": "not_required",
                "evaluated_at": evaluated_at.isoformat(),
                "reason": "Risk-reducing orders use the separate position and shorting checks",
                "blockers": [],
            }

        blockers: list[str] = []
        snapshot_summary: dict[str, Any] = {}
        reconciliation_summary: dict[str, Any] = {}
        scope = self.adapter.scope
        expected_scope = {
            "provider_id": "internal:paper-portfolio",
            "venue_id": "internal-ledger",
            "account_id": scope.account_id,
            "environment": ExecutionEnvironment.PAPER.value,
            "market_type": "spot",
        }
        observed_scope = {
            "provider_id": scope.provider_id,
            "venue_id": scope.venue_id,
            "account_id": scope.account_id,
            "environment": scope.environment.value,
            "market_type": scope.market_type,
        }
        if observed_scope != expected_scope or not scope.account_id:
            blockers.append("paper_account_scope_mismatch")

        try:
            snapshot = self.adapter.fetch_snapshot()
            if snapshot.scope.key != scope.key:
                blockers.append("snapshot_scope_mismatch")
            if snapshot.source != "internal-paper-ledger":
                blockers.append("snapshot_source_untrusted")
            fetched_at = self._timestamp(snapshot.fetched_at)
            age_seconds = (evaluated_at - fetched_at).total_seconds()
            if age_seconds < -self.future_tolerance_seconds:
                blockers.append("snapshot_timestamp_in_future")
            elif age_seconds > self.max_snapshot_age_seconds:
                blockers.append("snapshot_stale")

            balances_by_asset: dict[str, Decimal] = {}
            for balance in snapshot.balances:
                asset = balance.asset.strip().upper()
                if not asset or asset in balances_by_asset:
                    blockers.append("balance_identity_missing_or_duplicated")
                    continue
                free = self._decimal(balance.free, "balance_free")
                used = self._decimal(balance.used, "balance_used")
                total = self._decimal(balance.total, "balance_total")
                if min(free, used, total) < 0 or free + used != total:
                    blockers.append("balance_values_inconsistent")
                balances_by_asset[asset] = total
            if "EUR" not in balances_by_asset:
                blockers.append("eur_cash_balance_missing")

            position_ids: set[str] = set()
            for position in snapshot.positions:
                if (
                    not position.provider_id.strip()
                    or not position.instrument_id.strip()
                    or not position.symbol.strip()
                    or not position.quote_currency.strip()
                    or position.instrument_id in position_ids
                ):
                    blockers.append("position_identity_missing_or_duplicated")
                    continue
                position_ids.add(position.instrument_id)
                quantity = self._decimal(position.quantity, "position_quantity")
                if quantity <= 0:
                    blockers.append("position_quantity_invalid")
                if position.average_price is not None:
                    average_price = self._decimal(position.average_price, "position_average_price")
                    if average_price <= 0:
                        blockers.append("position_average_price_invalid")

            snapshot_summary = {
                "source": snapshot.source,
                "fetched_at": fetched_at.isoformat(),
                "age_seconds": round(age_seconds, 3),
                "balance_count": len(snapshot.balances),
                "position_count": len(snapshot.positions),
                "identity_basis": "local_internal_paper_ledger",
                "remote_identity_verified": False,
            }
        except (ScopedAccountAdapterError, AccountReadinessError, InvalidOperation, TypeError, ValueError):
            blockers.append("account_snapshot_invalid")
        except Exception:
            # Never expose adapter exceptions or potentially sensitive provider details.
            blockers.append("account_snapshot_unavailable")

        try:
            reconciliation = self.portfolio.reconcile_order_intents()
            intents = self.portfolio.list_order_intents(1000)
            unresolved = [
                item for item in intents
                if item.get("client_order_id") != exclude_client_order_id
                and item.get("status") not in self.TERMINAL_INTENT_STATES
            ]
            reconciliation_required = int(reconciliation.get("reconciliation_required", 0))
            if reconciliation_required > 0:
                blockers.append("paper_ledger_reconciliation_required")
            if unresolved:
                blockers.append("unresolved_paper_order_intents")
            reconciliation_summary = {
                "status": "clear" if not blockers and not unresolved else "blocked",
                "reconciled_filled": int(reconciliation.get("reconciled_filled", 0)),
                "reconciliation_required": reconciliation_required,
                "unresolved_intents": len(unresolved),
                "excluded_current_intent": bool(exclude_client_order_id),
            }
        except (PortfolioError, TypeError, ValueError):
            blockers.append("paper_ledger_reconciliation_failed")
            reconciliation_summary = {"status": "unknown"}

        # Preserve blocker order while avoiding duplicate diagnostics.
        blockers = list(dict.fromkeys(blockers))
        return {
            "status": "ready" if not blockers else "blocked",
            "evaluated_at": evaluated_at.isoformat(),
            "scope": observed_scope,
            "snapshot": snapshot_summary,
            "reconciliation": reconciliation_summary,
            "blockers": blockers,
        }

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .accounts.binance_demo import BinanceSpotDemoExecution
from .execution_registry import (
    CapabilityEvidence,
    CapabilityState,
    ExecutionCapabilities,
    ExecutionEnvironment,
    ExecutionScope,
)
from .portfolio import PaperPortfolio


class ScopedAccountAdapterError(ValueError):
    pass


def _finite_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ScopedAccountAdapterError(f"PAPER {field} is invalid") from exc
    if not parsed.is_finite():
        raise ScopedAccountAdapterError(f"PAPER {field} must be finite")
    return parsed


@dataclass(frozen=True)
class ScopedBalance:
    asset: str
    free: str | None
    used: str | None
    total: str | None


@dataclass(frozen=True)
class ScopedPosition:
    provider_id: str
    instrument_id: str
    symbol: str
    quantity: str
    average_price: str | None
    quote_currency: str


@dataclass(frozen=True)
class ScopedAccountSnapshot:
    scope: ExecutionScope
    fetched_at: str
    balances: tuple[ScopedBalance, ...]
    positions: tuple[ScopedPosition, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": {
                "provider_id": self.scope.provider_id,
                "venue_id": self.scope.venue_id,
                "account_id": self.scope.account_id,
                "environment": self.scope.environment.value,
                "market_type": self.scope.market_type,
            },
            "fetched_at": self.fetched_at,
            "source": self.source,
            "balances": [
                {
                    "asset": item.asset,
                    "free": item.free,
                    "used": item.used,
                    "total": item.total,
                }
                for item in self.balances
            ],
            "positions": [
                {
                    "provider_id": item.provider_id,
                    "instrument_id": item.instrument_id,
                    "symbol": item.symbol,
                    "quantity": item.quantity,
                    "average_price": item.average_price,
                    "quote_currency": item.quote_currency,
                }
                for item in self.positions
            ],
        }


class ScopedAccountAdapter(Protocol):
    scope: ExecutionScope

    def fetch_snapshot(self) -> ScopedAccountSnapshot: ...

    def capabilities(self) -> ExecutionCapabilities: ...


def _evidence(state: CapabilityState, source: str, detail: str | None = None) -> CapabilityEvidence:
    return CapabilityEvidence(
        state=state,
        source=source,
        verified_at=datetime.now(timezone.utc).isoformat(),
        detail=detail,
    )


class BinanceSpotDemoScopedAdapter:
    """Read/account-snapshot wrapper; preserves the dedicated Binance Demo order workflow."""

    def __init__(
        self,
        account: BinanceSpotDemoExecution,
        *,
        account_id: str,
    ) -> None:
        if not isinstance(account, BinanceSpotDemoExecution):
            raise ScopedAccountAdapterError("A Binance Spot Demo execution account is required")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ScopedAccountAdapterError("An explicit Binance Demo account scope is required")
        description = account.describe()
        if (
            description.get("provider_id") != account.provider_id
            or description.get("environment") != "demo"
            or description.get("demo_only") is not True
            or description.get("production_endpoint_allowed") is not False
        ):
            raise ScopedAccountAdapterError("Binance account is not positively identified as isolated Demo")
        self.account = account
        # The alias supplied by configuration scopes local state, but Binance
        # Spot account responses do not expose a stable account ID here.
        # Do not register this scope as identity-verified until a separate
        # provider identity proof is implemented.
        self.account_identity_verified = False
        self.scope = ExecutionScope(
            provider_id=account.provider_id,
            venue_id="binance",
            account_id=account_id.strip(),
            environment=ExecutionEnvironment.DEMO,
            market_type="spot",
        )

    def fetch_snapshot(self) -> ScopedAccountSnapshot:
        raw = self.account.fetch_balances()
        if not isinstance(raw, dict):
            raise ScopedAccountAdapterError("Binance Demo returned an invalid balance snapshot")
        if (
            raw.get("provider_id") != self.scope.provider_id
            or raw.get("environment") != self.scope.environment.value
            or raw.get("market_type") != self.scope.market_type
            or raw.get("demo_only") is not True
            or raw.get("withdrawals_enabled") is not False
        ):
            raise ScopedAccountAdapterError("Binance Demo balance response does not match the registered scope")
        raw_balances = raw.get("balances")
        if not isinstance(raw_balances, list):
            raise ScopedAccountAdapterError("Binance Demo balance collection is invalid")
        fetched_at = raw.get("fetched_at")
        if not isinstance(fetched_at, str):
            raise ScopedAccountAdapterError("Binance Demo snapshot timestamp is missing")
        try:
            parsed_timestamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScopedAccountAdapterError("Binance Demo snapshot timestamp is invalid") from exc
        if parsed_timestamp.tzinfo is None:
            raise ScopedAccountAdapterError("Binance Demo snapshot timestamp must include a timezone")
        balances = tuple(
            ScopedBalance(
                asset=item["asset"],
                free=item.get("free"),
                used=item.get("used"),
                total=item.get("total"),
            )
            for item in raw_balances
            if isinstance(item, dict)
        )
        if len(balances) != len(raw_balances):
            raise ScopedAccountAdapterError("Binance Demo balance record is malformed")
        return ScopedAccountSnapshot(
            scope=self.scope,
            fetched_at=parsed_timestamp.astimezone(timezone.utc).isoformat(),
            balances=balances,
            # Spot token balances are not silently re-labelled as broker positions.
            positions=(),
            source="binance-spot-demo",
        )

    def capabilities(self) -> ExecutionCapabilities:
        supported = lambda name: _evidence(CapabilityState.SUPPORTED, "binance-demo-adapter-contract", name)
        unsupported = lambda name: _evidence(CapabilityState.UNSUPPORTED, "binance-demo-adapter-contract", name)
        unknown = lambda name: _evidence(CapabilityState.UNKNOWN, "binance-demo-adapter-contract", name)
        return ExecutionCapabilities(
            asset_classes=("crypto",),
            order_types=("limit",),
            idempotent_client_order_id=supported("client order IDs are validated and reconciled"),
            partial_fills=supported("partial fill states and trade ledger are implemented"),
            cancellation=supported("dedicated Demo cancellation workflow"),
            order_reconciliation=supported("client order ID and exchange order reconciliation"),
            balances=supported("Spot Demo balance snapshots"),
            # A balance is not an instrument position. Until a canonical spot
            # position valuation contract exists, do not satisfy this readiness gate.
            positions=unknown("spot holdings require instrument-level position mapping"),
            withdrawals=unsupported("withdrawals are not exposed"),
            transfers=unsupported("transfers are not exposed"),
        )


class PaperPortfolioScopedAdapter:
    """Adapter over the internal PAPER ledger; never represents an external broker account."""

    def __init__(self, portfolio: PaperPortfolio, *, account_id: str) -> None:
        if not isinstance(portfolio, PaperPortfolio):
            raise ScopedAccountAdapterError("PaperPortfolio instance is required")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ScopedAccountAdapterError("An explicit PAPER account scope is required")
        self.portfolio = portfolio
        self.account_identity_verified = False
        self.scope = ExecutionScope(
            provider_id="internal:paper-portfolio",
            venue_id="internal-ledger",
            account_id=account_id.strip(),
            environment=ExecutionEnvironment.PAPER,
            market_type="spot",
        )

    def fetch_snapshot(self) -> ScopedAccountSnapshot:
        raw = self.portfolio.snapshot()
        if not isinstance(raw, dict):
            raise ScopedAccountAdapterError("PaperPortfolio returned an invalid snapshot")
        raw_balances = raw.get("cash_balances")
        raw_positions = raw.get("positions")
        if not isinstance(raw_balances, list) or not isinstance(raw_positions, list):
            raise ScopedAccountAdapterError("PAPER snapshot collections are invalid")

        normalized_balances = []
        for item in raw_balances:
            if not isinstance(item, dict) or not item.get("currency"):
                raise ScopedAccountAdapterError("PAPER cash balance record is malformed")
            amount = _finite_decimal(item.get("balance"), "cash balance")
            normalized_balances.append(
                ScopedBalance(
                    asset=str(item["currency"]).upper(),
                    free=str(amount),
                    used="0",
                    total=str(amount),
                )
            )
        balances = tuple(normalized_balances)

        normalized_positions = []
        for item in raw_positions:
            if not isinstance(item, dict):
                raise ScopedAccountAdapterError("PAPER position record is malformed")
            quantity = _finite_decimal(item.get("quantity"), "position quantity")
            if quantity < 0:
                raise ScopedAccountAdapterError("PAPER spot position quantity cannot be negative")
            if quantity == 0:
                continue
            provider_id = item.get("provider_id")
            instrument_id = item.get("instrument_id")
            symbol = item.get("symbol")
            quote_currency = item.get("quote_currency") or "EUR"
            if not all(isinstance(value, str) and value.strip() for value in (provider_id, instrument_id, symbol, quote_currency)):
                raise ScopedAccountAdapterError("PAPER position identity is incomplete")
            average_price = item.get("average_price")
            if average_price is not None:
                average_price = _finite_decimal(average_price, "average price")
                if average_price <= 0:
                    raise ScopedAccountAdapterError("PAPER average price must be positive")
            normalized_positions.append(
                ScopedPosition(
                    provider_id=provider_id,
                    instrument_id=instrument_id,
                    symbol=symbol,
                    quantity=str(quantity),
                    average_price=str(average_price) if average_price is not None else None,
                    quote_currency=quote_currency.upper(),
                )
            )
        positions = tuple(normalized_positions)
        return ScopedAccountSnapshot(
            scope=self.scope,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            balances=balances,
            positions=positions,
            source="internal-paper-ledger",
        )

    def capabilities(self) -> ExecutionCapabilities:
        supported = lambda name: _evidence(CapabilityState.SUPPORTED, "paper-portfolio-contract", name)
        unsupported = lambda name: _evidence(CapabilityState.UNSUPPORTED, "paper-portfolio-contract", name)
        return ExecutionCapabilities(
            asset_classes=("crypto",),
            order_types=("market",),
            idempotent_client_order_id=supported("durable unique order intent"),
            partial_fills=unsupported("internal PAPER fills are atomic; no partial fill simulator"),
            cancellation=unsupported("PAPER intents are not exchange orders"),
            order_reconciliation=supported("order intents reconcile against atomic trade ledger"),
            balances=supported("internal cash balances"),
            positions=supported("provider/instrument-scoped ledger positions"),
            withdrawals=unsupported("not an external account"),
            transfers=unsupported("not an external account"),
        )

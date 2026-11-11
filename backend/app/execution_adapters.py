from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
        self.scope = ExecutionScope(
            provider_id=account.provider_id,
            venue_id="binance",
            account_id=account_id.strip(),
            environment=ExecutionEnvironment.DEMO,
            market_type="spot",
        )

    def fetch_snapshot(self) -> ScopedAccountSnapshot:
        raw = self.account.fetch_balances()
        if (
            raw.get("provider_id") != self.scope.provider_id
            or raw.get("environment") != self.scope.environment.value
            or raw.get("market_type") != self.scope.market_type
            or raw.get("demo_only") is not True
            or raw.get("withdrawals_enabled") is not False
        ):
            raise ScopedAccountAdapterError("Binance Demo balance response does not match the registered scope")
        balances = tuple(
            ScopedBalance(
                asset=item["asset"],
                free=item.get("free"),
                used=item.get("used"),
                total=item.get("total"),
            )
            for item in raw.get("balances", [])
        )
        return ScopedAccountSnapshot(
            scope=self.scope,
            fetched_at=raw["fetched_at"],
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
        balances = tuple(
            ScopedBalance(
                asset=str(item["currency"]).upper(),
                free=str(item["balance"]),
                used="0",
                total=str(item["balance"]),
            )
            for item in raw.get("cash_balances", [])
        )
        positions = tuple(
            ScopedPosition(
                provider_id=str(item["provider_id"]),
                instrument_id=str(item["instrument_id"]),
                symbol=str(item["symbol"]),
                quantity=str(item["quantity"]),
                average_price=str(item["average_price"]) if item.get("average_price") is not None else None,
                quote_currency=str(item.get("quote_currency") or "EUR").upper(),
            )
            for item in raw.get("positions", [])
            if float(item.get("quantity") or 0) > 0
        )
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
            asset_classes=("crypto", "equity", "etf"),
            order_types=("market", "limit"),
            idempotent_client_order_id=supported("durable unique order intent"),
            partial_fills=unsupported("internal PAPER fills are atomic; no partial fill simulator"),
            cancellation=unsupported("PAPER intents are not exchange orders"),
            order_reconciliation=supported("order intents reconcile against atomic trade ledger"),
            balances=supported("internal cash balances"),
            positions=supported("provider/instrument-scoped ledger positions"),
            withdrawals=unsupported("not an external account"),
            transfers=unsupported("not an external account"),
        )

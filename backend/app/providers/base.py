from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Protocol, runtime_checkable


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    COMMODITY = "commodity"
    FUTURE = "future"
    OPTION = "option"
    DEFI = "defi"
    TOKENIZED = "tokenized"


class VenueType(str, Enum):
    CEX = "cex"
    BROKER = "broker"
    DEX = "dex"
    WALLET = "wallet"
    DATA = "market_data"


class MarketType(str, Enum):
    SPOT = "spot"
    CASH_EQUITY = "cash_equity"
    MARGIN = "margin"
    FUTURES = "futures"
    OPTIONS = "options"
    FX = "forex"
    ONCHAIN = "onchain"


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    venue_type: VenueType
    asset_classes: tuple[AssetClass, ...]
    market_types: tuple[MarketType, ...]
    public_market_data: bool
    requires_authentication_for_market_data: bool = False
    requires_authentication_for_trading: bool = True
    connectivity_status: str = "not_checked"
    rate_limit_ms: int | None = None
    geographic_restrictions: tuple[str, ...] = ()
    account_eligibility: tuple[str, ...] = ()
    authenticated_account: bool = False
    balances: bool = False
    positions: bool = False
    order_management: bool = False
    supports_market_orders: bool = False
    supports_limit_orders: bool = False
    supports_stop_orders: bool = False
    supports_client_order_id: bool = False
    supports_partial_fills: bool = False
    supports_cancel: bool = False
    supports_reconciliation: bool = False
    supports_withdrawals: bool = False
    supports_wallet_signing: bool = False
    supported_symbols: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()


class PrecisionMode(str, Enum):
    UNKNOWN = "unknown"
    DECIMAL_PLACES = "decimal_places"
    TICK_SIZE = "tick_size"
    SIGNIFICANT_DIGITS = "significant_digits"


@dataclass(frozen=True)
class MarketInstrument:
    """Provider-normalized instrument contract; missing provider facts stay explicit."""

    provider_id: str
    symbol: str
    asset_class: AssetClass
    market_type: MarketType
    base: str
    quote: str
    active: bool
    min_amount: float | None = None
    min_cost: float | None = None
    maker_fee: float | None = None
    taker_fee: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    venue_id: str | None = None
    native_symbol: str | None = None
    precision_mode: PrecisionMode = PrecisionMode.UNKNOWN
    price_precision: int | None = None
    amount_precision: int | None = None
    price_tick: float | None = None
    amount_step: float | None = None
    max_amount: float | None = None
    max_cost: float | None = None
    contract_type: str | None = None
    contract_size: float | None = None
    settlement_currency: str | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None
    underlying: str | None = None
    trading_status: str | None = None
    session_timezone: str | None = None

    def __post_init__(self) -> None:
        for enum_name, enum_type in (("asset_class", AssetClass), ("market_type", MarketType)):
            value = getattr(self, enum_name)
            if not isinstance(value, enum_type):
                try:
                    object.__setattr__(self, enum_name, enum_type(value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Unsupported instrument {enum_name}") from exc
        for name in ("provider_id", "symbol", "base", "quote"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Instrument {name} is required")
        if not isinstance(self.active, bool):
            raise ValueError("Instrument active flag must be boolean")

        for name in ("min_amount", "min_cost", "max_amount", "max_cost", "price_tick", "amount_step", "contract_size", "strike", "maker_fee", "taker_fee"):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Instrument {name} must be finite") from exc
            if not math.isfinite(numeric):
                raise ValueError(f"Instrument {name} must be finite")
            if name in {"price_tick", "amount_step", "contract_size", "strike"} and numeric <= 0:
                raise ValueError(f"Instrument {name} must be positive")
            if name in {"min_amount", "min_cost", "max_amount", "max_cost"} and numeric < 0:
                raise ValueError(f"Instrument {name} cannot be negative")
            object.__setattr__(self, name, numeric)

        for name in ("price_precision", "amount_precision"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"Instrument {name} must be a non-negative integer")

        if self.max_amount is not None and self.min_amount is not None and self.max_amount < self.min_amount:
            raise ValueError("Instrument max_amount cannot be below min_amount")
        if self.max_cost is not None and self.min_cost is not None and self.max_cost < self.min_cost:
            raise ValueError("Instrument max_cost cannot be below min_cost")

        if not isinstance(self.precision_mode, PrecisionMode):
            try:
                object.__setattr__(self, "precision_mode", PrecisionMode(self.precision_mode))
            except (TypeError, ValueError) as exc:
                raise ValueError("Unsupported instrument precision mode") from exc

        if self.native_symbol is None:
            object.__setattr__(self, "native_symbol", self.symbol)
        for name in ("native_symbol", "venue_id", "contract_type", "settlement_currency", "expiry", "underlying", "session_timezone"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Instrument {name} cannot be blank or non-text")

        status = self.trading_status
        if status is None:
            object.__setattr__(self, "trading_status", "active" if self.active else "inactive")
        elif not isinstance(status, str) or status not in {"active", "inactive", "suspended", "expired", "unknown"}:
            raise ValueError("Unsupported instrument trading_status")

        if self.option_type is not None:
            if not isinstance(self.option_type, str):
                raise ValueError("option_type must be call or put")
            normalized_option_type = self.option_type.strip().lower()
            if self.asset_class != AssetClass.OPTION or normalized_option_type not in {"call", "put"}:
                raise ValueError("option_type is only valid as call or put for option instruments")
            object.__setattr__(self, "option_type", normalized_option_type)
        if self.asset_class == AssetClass.OPTION:
            if not isinstance(self.expiry, str) or not self.expiry.strip() or not isinstance(self.underlying, str) or not self.underlying.strip():
                raise ValueError("Option instruments require expiry, positive strike and underlying")
            if self.option_type not in {"call", "put"} or self.strike is None:
                raise ValueError("Option instruments require option_type call or put and a positive strike")
            from datetime import date, datetime
            try:
                datetime.fromisoformat(self.expiry.replace("Z", "+00:00"))
            except ValueError:
                try:
                    date.fromisoformat(self.expiry)
                except ValueError as exc:
                    raise ValueError("Option expiry must be an ISO date or datetime") from exc
        elif self.strike is not None:
            raise ValueError("strike is only valid for option instruments")

    @property
    def instrument_id(self) -> str:
        # Preserve the legacy key for existing portfolio rows. Some providers
        # (notably data aggregators) can expose the same ticker on multiple MICs;
        # a venue-aware canonical key requires a separate, explicit DB migration.
        return f"{self.provider_id}:{self.market_type.value}:{self.symbol}"


@dataclass(frozen=True)
class MarketQuote:
    provider_id: str
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    timestamp_ms: int
    bid_volume: float | None = None
    ask_volume: float | None = None


@runtime_checkable
class MarketDataProvider(Protocol):
    provider_id: str

    def capabilities(self) -> ProviderCapabilities: ...
    def discover_markets(self, asset_classes: tuple[AssetClass, ...] | None = None) -> list[MarketInstrument]: ...
    def ticker(self, symbol: str) -> dict: ...
    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list: ...


@runtime_checkable
class ExecutionProvider(Protocol):
    provider_id: str

    def capabilities(self) -> ProviderCapabilities: ...
    def validate_order(self, instrument: MarketInstrument, order: dict) -> dict: ...
    def submit_order(self, order: dict, client_order_id: str) -> dict: ...
    def get_order(self, instrument: MarketInstrument, order_id: str) -> dict | None: ...
    def cancel_order(self, instrument: MarketInstrument, order_id: str) -> dict: ...
    def get_balances(self) -> dict: ...
    def get_positions(self) -> list[dict]: ...
    def reconcile(self) -> dict: ...


@dataclass(frozen=True)
class WalletBalance:
    chain_id: str
    address: str
    asset: str
    quantity: float
    token_address: str | None = None
    block_number: int | None = None


@runtime_checkable
class ReadOnlyWalletProvider(Protocol):
    """Public-address access only; never accepts seed phrases or private keys."""

    provider_id: str

    def get_public_balances(self, address: str) -> list[WalletBalance]: ...
    def discover_tokens(self, address: str) -> list[dict[str, Any]]: ...
    def transactions(self, address: str, limit: int = 100) -> list[dict[str, Any]]: ...
    def estimate_gas(self, transaction: dict[str, Any]) -> dict[str, Any]: ...
    def simulate_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class IsolatedTransactionSigner(Protocol):
    """Signing must live outside AI agents and require an owner authorization."""

    signer_id: str

    def sign_transaction(self, transaction: dict[str, Any], owner_authorization_id: str) -> str: ...

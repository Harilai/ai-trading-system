from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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


@dataclass(frozen=True)
class MarketInstrument:
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

    @property
    def instrument_id(self) -> str:
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

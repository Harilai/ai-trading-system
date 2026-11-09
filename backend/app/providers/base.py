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

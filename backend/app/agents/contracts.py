from typing import Literal

from pydantic import BaseModel, Field


class MarketAnalysis(BaseModel):
    symbol: str
    timeframe: str
    as_of_ms: int = Field(gt=0)
    close: float = Field(gt=0)
    indicators: dict[str, float]


class TradeProposal(BaseModel):
    symbol: str
    provider_id: str = "ccxt:kraken"
    instrument_id: str | None = None
    asset_class: Literal["crypto", "forex", "equity", "etf", "index", "commodity", "future", "option", "defi", "tokenized"] = "crypto"
    market_type: Literal["spot", "cash_equity", "margin", "futures", "options", "forex", "onchain"] = "spot"
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    rationale: str = Field(min_length=10, max_length=2000)
    source_agent: str = Field(default="sniper", min_length=1, max_length=64)


class RiskContext(BaseModel):
    equity_eur: float = Field(gt=0)
    available_cash_eur: float = Field(ge=0)
    max_order_fraction: float = Field(default=0.25, gt=0, le=1)
    max_position_fraction: float = Field(default=0.20, gt=0, le=1)
    max_stop_distance_fraction: float = Field(default=0.05, gt=0, le=1)
    estimated_fee_rate: float = Field(default=0.0026, ge=0, lt=1)
    estimated_slippage_rate: float = Field(default=0.0005, ge=0, lt=1)
    quote_currency: str = "EUR"
    fx_rate_to_eur: float = Field(default=1.0, gt=0)
    available_cash_by_currency: dict[str, float] = Field(default_factory=dict)
    positions: dict[str, float] = Field(default_factory=dict)
    position_values_eur: dict[str, float] = Field(default_factory=dict)


class RiskDecision(BaseModel):
    accepted: bool
    reason: str
    approved_quantity: float = Field(default=0, ge=0)
    estimated_notional_eur: float = Field(default=0, ge=0)

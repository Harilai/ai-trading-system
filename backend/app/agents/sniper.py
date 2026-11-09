from __future__ import annotations

from typing import Any

from .agents.contracts import TradeProposal


class SniperAgent:
    """Builds bounded entry proposals; the Risk Manager remains the final gate."""

    def __init__(
        self,
        *,
        max_order_fraction: float = 0.25,
        risk_per_trade_fraction: float = 0.01,
        estimated_fee_rate: float = 0.0026,
        estimated_slippage_rate: float = 0.0005,
        minimum_order_value: float = 1.0,
        atr_stop_multiple: float = 2.0,
        reward_risk_ratio: float = 2.0,
    ):
        if not 0 < max_order_fraction <= 1:
            raise ValueError("max_order_fraction must be in (0, 1]")
        if not 0 < risk_per_trade_fraction <= max_order_fraction:
            raise ValueError("risk_per_trade_fraction must be in (0, max_order_fraction]")
        if not 0 <= estimated_fee_rate < 1 or not 0 <= estimated_slippage_rate < 1:
            raise ValueError("Fee and slippage estimates must be in [0, 1)")
        if minimum_order_value <= 0 or atr_stop_multiple <= 0 or reward_risk_ratio <= 0:
            raise ValueError("Minimum order value and ATR/reward-risk multiples must be positive")
        self.max_order_fraction = max_order_fraction
        self.risk_per_trade_fraction = risk_per_trade_fraction
        self.estimated_fee_rate = estimated_fee_rate
        self.estimated_slippage_rate = estimated_slippage_rate
        self.minimum_order_value = minimum_order_value
        self.atr_stop_multiple = atr_stop_multiple
        self.reward_risk_ratio = reward_risk_ratio

    def propose(
        self,
        market: dict[str, Any],
        analysis: dict[str, Any],
        candidate: dict[str, Any],
        *,
        equity: float,
        available_cash: float,
    ) -> TradeProposal | None:
        """Size an eligible long-only spot entry from stop distance, allocation and cash."""
        if not candidate.get("eligible") or not candidate.get("current_entry_signal"):
            return None
        score = candidate.get("score")
        if not isinstance(score, (int, float)):
            return None
        if equity <= 0 or available_cash <= 0:
            return None

        symbol = str(market.get("symbol") or "").strip().upper()
        if not symbol.endswith("/EUR"):
            return None
        price = float(market.get("ask") or market.get("last") or analysis.get("close") or 0)
        atr_value = float(analysis.get("indicators", {}).get("atr_14", 0) or 0)
        if price <= 0 or atr_value <= 0:
            return None

        stop_distance = self.atr_stop_multiple * atr_value
        stop_loss = price - stop_distance
        take_profit = price + self.reward_risk_ratio * stop_distance
        if stop_loss <= 0:
            return None

        risk_budget = equity * self.risk_per_trade_fraction
        risk_quantity = risk_budget / stop_distance
        allocation_quantity = (
            equity * self.max_order_fraction
            / (price * (1.0 + self.estimated_fee_rate))
        )
        cash_quantity = available_cash / (
            price
            * (1.0 + self.estimated_fee_rate)
            * (1.0 + self.estimated_slippage_rate)
        )
        quantity = min(risk_quantity, allocation_quantity, cash_quantity)
        if quantity <= 0 or quantity * price < self.minimum_order_value:
            return None

        provider_id = str(market.get("provider_id") or "ccxt:kraken")
        instrument_id = str(
            market.get("instrument_id") or f"{provider_id}:spot:{symbol}"
        )
        strategy_id = str(candidate.get("strategy_id") or "unknown")
        return TradeProposal(
            symbol=symbol,
            provider_id=provider_id,
            instrument_id=instrument_id,
            side="buy",
            quantity=quantity,
            reference_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            rationale=(
                f"{strategy_id} entry; OOS score {float(score):.6f}; "
                f"risk budget {self.risk_per_trade_fraction:.2%}"
            ),
            source_agent="sniper",
        )

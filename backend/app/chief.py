from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .agents.contracts import RiskContext, RiskDecision, TradeProposal
from .agents.risk import RiskManager


class ChiefDecision(BaseModel):
    action: Literal["hold", "buy", "sell"] = "hold"
    symbol: str | None = None
    strategy_id: str | None = None
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    proposal: TradeProposal | None = None
    risk: RiskDecision | None = None


class ChiefAgent:
    """Select validated opportunities and require deterministic risk approval."""

    def __init__(
        self,
        *,
        risk_manager: RiskManager | None = None,
        max_order_fraction: float = 0.25,
        max_stop_distance_fraction: float = 0.05,
        risk_per_trade_fraction: float = 0.01,
        estimated_fee_rate: float = 0.0026,
        estimated_slippage_rate: float = 0.0005,
        minimum_order_value_eur: float = 1.0,
    ):
        if not 0 < max_order_fraction <= 1:
            raise ValueError("max_order_fraction must be in (0, 1]")
        if not 0 < max_stop_distance_fraction <= 1:
            raise ValueError("max_stop_distance_fraction must be in (0, 1]")
        if not 0 < risk_per_trade_fraction <= max_order_fraction:
            raise ValueError("risk_per_trade_fraction must be in (0, max_order_fraction]")
        if not 0 <= estimated_fee_rate < 1 or not 0 <= estimated_slippage_rate < 1:
            raise ValueError("Fee and slippage estimates must be in [0, 1)")
        if minimum_order_value_eur <= 0:
            raise ValueError("minimum_order_value_eur must be positive")
        self.risk_manager = risk_manager or RiskManager()
        self.max_order_fraction = max_order_fraction
        self.max_stop_distance_fraction = max_stop_distance_fraction
        self.risk_per_trade_fraction = risk_per_trade_fraction
        self.estimated_fee_rate = estimated_fee_rate
        self.estimated_slippage_rate = estimated_slippage_rate
        self.minimum_order_value_eur = minimum_order_value_eur

    @staticmethod
    def _candidate_for(item: dict[str, Any], strategy_id: str | None = None):
        research = item.get("strategy_research", {})
        wanted = strategy_id or research.get("recommended_strategy")
        if not wanted:
            return None
        return next(
            (
                candidate for candidate in research.get("candidates", [])
                if candidate.get("strategy_id") == wanted and candidate.get("eligible")
            ),
            None,
        )

    def decide(self, scan: dict[str, Any], portfolio: dict[str, Any]) -> ChiefDecision:
        portfolio_positions = {
            position["symbol"].upper(): position
            for position in portfolio.get("positions", [])
        }
        positions = {
            symbol: float(position["quantity"])
            for symbol, position in portfolio_positions.items()
        }
        equity = float(portfolio.get("equity", 0))
        cash = float(portfolio.get("cash", 0))
        if equity <= 0 or cash < 0:
            return ChiefDecision(rationale="Portfolio equity or cash is invalid; no trade proposed")

        context = RiskContext(
            equity_eur=equity,
            available_cash_eur=cash,
            max_order_fraction=self.max_order_fraction,
            max_stop_distance_fraction=self.max_stop_distance_fraction,
            estimated_fee_rate=self.estimated_fee_rate,
            positions=positions,
        )
        scan_by_symbol = {
            str(item.get("market", {}).get("symbol", "")).upper(): item
            for item in scan.get("analyses", [])
        }

        # Manage existing positions before considering new entries.
        for symbol, position in portfolio_positions.items():
            item = scan_by_symbol.get(symbol)
            if item is None:
                continue
            market = item.get("market", {})
            sell_price = float(market.get("bid") or market.get("last") or 0)
            if sell_price <= 0:
                continue
            stop_loss = position.get("stop_loss")
            take_profit = position.get("take_profit")
            exit_reason = None
            if stop_loss is not None and sell_price <= float(stop_loss):
                exit_reason = "persisted stop-loss triggered"
            elif take_profit is not None and sell_price >= float(take_profit):
                exit_reason = "persisted take-profit triggered"
            else:
                candidate = self._candidate_for(item, position.get("strategy_id"))
                if candidate is not None and not candidate.get("current_hold_signal", False):
                    exit_reason = "validated strategy exit signal"
            if exit_reason is None:
                continue

            proposal = TradeProposal(
                symbol=symbol,
                side="sell",
                quantity=positions[symbol],
                reference_price=sell_price,
                rationale=exit_reason,
                source_agent="chief",
            )
            risk = self.risk_manager.evaluate(proposal, context)
            if risk.accepted:
                return ChiefDecision(
                    action="sell",
                    symbol=symbol,
                    strategy_id=position.get("strategy_id"),
                    rationale=f"Position exit: {exit_reason}; Risk Manager approved",
                    evidence={"market": market, "stop_loss": stop_loss, "take_profit": take_profit},
                    proposal=proposal,
                    risk=risk,
                )
            return ChiefDecision(
                symbol=symbol,
                strategy_id=position.get("strategy_id"),
                rationale="Position exit proposal rejected by Risk Manager",
                evidence={"market": market, "exit_reason": exit_reason},
                proposal=proposal,
                risk=risk,
            )

        if positions and not portfolio.get("valuation_complete", False):
            return ChiefDecision(
                rationale="Market valuation is incomplete for one or more open positions; no new entry is allowed",
                evidence={"open_positions": list(positions)},
            )

        opportunities = []
        for item in scan.get("analyses", []):
            research = item.get("strategy_research", {})
            strategy_id = research.get("recommended_strategy")
            candidate = self._candidate_for(item, strategy_id)
            if candidate is None:
                continue
            market = item.get("market", {})
            symbol = str(market.get("symbol", "")).upper()
            score = candidate.get("score")
            if (
                not symbol.endswith("/EUR")
                or symbol in positions
                or not isinstance(score, (int, float))
            ):
                continue
            opportunities.append((float(score), item, candidate, strategy_id, symbol))

        opportunities.sort(key=lambda row: row[0], reverse=True)
        for score, item, candidate, strategy_id, symbol in opportunities:
            market = item["market"]
            analysis = item["analysis"]
            price = float(market.get("ask") or market.get("last") or analysis.get("close") or 0)
            atr_value = float(analysis.get("indicators", {}).get("atr_14", 0) or 0)
            if price <= 0 or not candidate.get("current_entry_signal", False):
                continue
            stop_distance = 2.0 * atr_value
            stop_loss = price - stop_distance
            take_profit = price + 2.0 * stop_distance
            if atr_value <= 0 or stop_loss <= 0:
                continue
            risk_budget = equity * self.risk_per_trade_fraction
            risk_quantity = risk_budget / stop_distance
            allocation_quantity = equity * self.max_order_fraction / (
                price * (1.0 + self.estimated_fee_rate)
            )
            cash_quantity = cash / (
                price * (1.0 + self.estimated_fee_rate) * (1.0 + self.estimated_slippage_rate)
            )
            quantity = min(risk_quantity, allocation_quantity, cash_quantity)
            if quantity <= 0 or quantity * price < self.minimum_order_value_eur:
                continue

            proposal = TradeProposal(
                symbol=symbol,
                side="buy",
                quantity=quantity,
                reference_price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"{strategy_id} entry; OOS score {score:.6f}; risk budget {self.risk_per_trade_fraction:.2%}",
                source_agent="chief",
            )
            risk = self.risk_manager.evaluate(proposal, context)
            if risk.accepted:
                return ChiefDecision(
                    action="buy",
                    symbol=symbol,
                    strategy_id=strategy_id,
                    rationale="Best validated entry opportunity passed deterministic risk checks",
                    evidence={
                        "score": score,
                        "market": market,
                        "out_of_sample": candidate["out_of_sample"],
                        "risk_budget_eur": risk_budget,
                        "stop_distance": stop_distance,
                    },
                    proposal=proposal,
                    risk=risk,
                )
            return ChiefDecision(
                symbol=symbol,
                strategy_id=strategy_id,
                rationale="Entry proposal rejected by Risk Manager",
                evidence={"score": score},
                proposal=proposal,
                risk=risk,
            )

        return ChiefDecision(
            rationale="No eligible strategy produced a valid entry or exit signal; hold cash and monitor",
            evidence={"markets_analysed": len(scan.get("analyses", [])), "eligible_opportunities": len(opportunities)},
        )

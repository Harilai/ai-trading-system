from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .agents.contracts import RiskContext, RiskDecision, TradeProposal
from .agents.risk import RiskManager
from .agents.sniper import SniperAgent


class ChiefDecision(BaseModel):
    action: Literal["hold", "buy", "sell"] = "hold"
    symbol: str | None = None
    provider_id: str | None = None
    instrument_id: str | None = None
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
        sniper: SniperAgent | None = None,
        max_order_fraction: float = 0.25,
        max_stop_distance_fraction: float = 0.05,
        risk_per_trade_fraction: float = 0.01,
        estimated_fee_rate: float = 0.0026,
        estimated_slippage_rate: float = 0.0005,
        minimum_order_value_eur: float = 1.0,
        allow_experimental_strategies: bool = False,
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
        self.sniper = sniper or SniperAgent(
            max_order_fraction=max_order_fraction,
            risk_per_trade_fraction=risk_per_trade_fraction,
            estimated_fee_rate=estimated_fee_rate,
            estimated_slippage_rate=estimated_slippage_rate,
            minimum_order_value=minimum_order_value_eur,
        )
        self.max_order_fraction = max_order_fraction
        self.max_stop_distance_fraction = max_stop_distance_fraction
        self.risk_per_trade_fraction = risk_per_trade_fraction
        self.estimated_fee_rate = estimated_fee_rate
        self.estimated_slippage_rate = estimated_slippage_rate
        self.minimum_order_value_eur = minimum_order_value_eur
        self.allow_experimental_strategies = bool(allow_experimental_strategies)

    @staticmethod
    def _instrument_identity(provider_id: str, instrument_id: str | None, symbol: str) -> str:
        return instrument_id or f"{provider_id}:spot:{symbol.upper()}"

    @staticmethod
    def _candidate_for(
        item: dict[str, Any],
        strategy_id: str | None = None,
        *,
        allow_experimental: bool = False,
    ):
        research = item.get("strategy_research", {})
        wanted = strategy_id or research.get("recommended_strategy")
        if isinstance(wanted, str) and wanted.startswith("experimental:"):
            wanted = wanted.removeprefix("experimental:")
            allow_experimental = True
        if not wanted and allow_experimental:
            wanted = research.get("recommended_experimental_strategy")
        if not wanted:
            return None
        return next(
            (
                candidate for candidate in research.get("candidates", [])
                if candidate.get("strategy_id") == wanted
                and (
                    candidate.get("eligible")
                    or (allow_experimental and candidate.get("experimental_eligible"))
                )
            ),
            None,
        )

    def decide(self, scan: dict[str, Any], portfolio: dict[str, Any]) -> ChiefDecision:
        if portfolio.get("valuation_complete") is False:
            return ChiefDecision(
                rationale="Portfolio valuation is incomplete; no autonomous order is permitted",
                evidence={"valuation_complete": False},
            )
        portfolio_positions = {}
        for position in portfolio.get("positions", []):
            symbol = position["symbol"].upper()
            provider_id = position.get("provider_id") or "ccxt:kraken"
            identity = self._instrument_identity(provider_id, position.get("instrument_id"), symbol)
            portfolio_positions[identity] = position
        positions = {
            identity: float(position["quantity"])
            for identity, position in portfolio_positions.items()
        }
        equity = float(portfolio.get("equity", 0))
        cash = float(
            portfolio.get(
                "available_cash_for_manual_withdrawal",
                portfolio.get("cash", 0),
            )
        )
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
        scan_by_identity = {}
        for item in scan.get("analyses", []):
            market = item.get("market", {})
            symbol = str(market.get("symbol", "")).upper()
            provider_id = market.get("provider_id") or "ccxt:kraken"
            identity = self._instrument_identity(provider_id, market.get("instrument_id"), symbol)
            scan_by_identity[identity] = item

        # Manage existing positions before considering new entries.
        for identity, position in portfolio_positions.items():
            symbol = position["symbol"].upper()
            item = scan_by_identity.get(identity)
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
                candidate = self._candidate_for(
                    item,
                    position.get("strategy_id"),
                    allow_experimental=self.allow_experimental_strategies,
                )
                if candidate is not None and not candidate.get("current_hold_signal", False):
                    exit_reason = "validated strategy exit signal"
            if exit_reason is None:
                continue

            proposal = TradeProposal(
                symbol=symbol,
                provider_id=str(market.get("provider_id") or position.get("provider_id") or "ccxt:kraken"),
                instrument_id=str(market.get("instrument_id") or position.get("instrument_id") or identity),
                side="sell",
                quantity=positions[identity],
                reference_price=sell_price,
                rationale=exit_reason,
                source_agent="chief",
            )
            risk = self.risk_manager.evaluate(proposal, context)
            if risk.accepted:
                return ChiefDecision(
                    action="sell",
                    symbol=symbol,
                    provider_id=proposal.provider_id,
                    instrument_id=proposal.instrument_id,
                    strategy_id=position.get("strategy_id"),
                    rationale=f"Position exit: {exit_reason}; Risk Manager approved",
                    evidence={"market": market, "stop_loss": stop_loss, "take_profit": take_profit},
                    proposal=proposal,
                    risk=risk,
                )
            return ChiefDecision(
                symbol=symbol,
                provider_id=proposal.provider_id,
                instrument_id=proposal.instrument_id,
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
            experimental = False
            if not strategy_id and self.allow_experimental_strategies:
                strategy_id = research.get("recommended_experimental_strategy")
                experimental = bool(strategy_id)
            candidate = self._candidate_for(
                item,
                strategy_id,
                allow_experimental=experimental,
            )
            if candidate is None:
                continue
            market = item.get("market", {})
            symbol = str(market.get("symbol", "")).upper()
            score = (
                candidate.get("experimental_score")
                if experimental else candidate.get("score")
            )
            provider_id = str(market.get("provider_id") or "ccxt:kraken")
            identity = self._instrument_identity(provider_id, market.get("instrument_id"), symbol)
            if (
                not symbol.endswith("/EUR")
                or identity in positions
                or not isinstance(score, (int, float))
            ):
                continue
            opportunities.append((float(score), item, candidate, strategy_id, symbol, experimental))

        opportunities.sort(key=lambda row: row[0], reverse=True)
        for score, item, candidate, strategy_id, symbol, experimental in opportunities:
            market = item["market"]
            analysis = item["analysis"]
            proposal = self.sniper.propose(
                market,
                analysis,
                candidate,
                equity=equity,
                available_cash=cash,
                allow_experimental=experimental,
            )
            if proposal is None:
                continue
            risk = self.risk_manager.evaluate(proposal, context)
            if risk.accepted:
                return ChiefDecision(
                    action="buy",
                    symbol=symbol,
                    provider_id=proposal.provider_id,
                    instrument_id=proposal.instrument_id,
                    strategy_id=(f"experimental:{strategy_id}" if experimental else strategy_id),
                    rationale=(
                        "Experimental paper-only entry; strategy is not validated for profitability"
                        if experimental else
                        "Best validated entry opportunity passed deterministic risk checks"
                    ),
                    evidence={
                        "score": score,
                        "strategy_validation_status": "experimental" if experimental else "validated",
                        "validation_failures": (
                            candidate.get("validation_failures", []) if experimental else []
                        ),
                        "market": market,
                        "out_of_sample": candidate["out_of_sample"],
                        "risk_budget_eur": equity * self.risk_per_trade_fraction,
                        "stop_distance": proposal.reference_price - proposal.stop_loss,
                    },
                    proposal=proposal,
                    risk=risk,
                )
            return ChiefDecision(
                symbol=symbol,
                provider_id=proposal.provider_id,
                instrument_id=proposal.instrument_id,
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

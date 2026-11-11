from __future__ import annotations

from typing import Any, Literal
import math

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
        require_account_readiness: bool = False,
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
        self.require_account_readiness = bool(require_account_readiness)

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

    def decide(
        self,
        scan: dict[str, Any],
        portfolio: dict[str, Any],
        *,
        account_readiness: dict[str, Any] | None = None,
    ) -> ChiefDecision:
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

        diagnostics = {
            "markets_analysed": len(scan.get("analyses", [])),
            "open_positions": len(positions),
            "validated_candidates": 0,
            "experimental_candidates": 0,
            "validated_entry_signals": 0,
            "experimental_entry_signals": 0,
            "candidates_without_entry_signal": 0,
            "excluded_non_eur_symbols": 0,
            "excluded_open_positions": 0,
            "excluded_missing_price": 0,
            "sniper_no_proposal": 0,
            "risk_rejections": 0,
            "validation_failure_counts": {"promotion": {}, "experimental": {}},
        }
        for analysed_item in scan.get("analyses", []):
            research_candidates = analysed_item.get("strategy_research", {}).get("candidates", [])
            for research_candidate in research_candidates:
                validated = bool(research_candidate.get("eligible"))
                experimental_candidate = bool(research_candidate.get("experimental_eligible"))
                entry_signal = bool(research_candidate.get("current_entry_signal"))
                for gate, field in (
                    ("promotion", "validation_failures"),
                    ("experimental", "experimental_validation_failures"),
                ):
                    for failure in research_candidate.get(field) or []:
                        counts = diagnostics["validation_failure_counts"][gate]
                        failure_code = str(failure)
                        counts[failure_code] = counts.get(failure_code, 0) + 1
                if validated:
                    diagnostics["validated_candidates"] += 1
                    if entry_signal:
                        diagnostics["validated_entry_signals"] += 1
                if experimental_candidate:
                    diagnostics["experimental_candidates"] += 1
                    if entry_signal:
                        diagnostics["experimental_entry_signals"] += 1
                if (validated or experimental_candidate) and not entry_signal:
                    diagnostics["candidates_without_entry_signal"] += 1

        # Manage existing positions before considering new entries. Missing or
        # malformed strategy state must never be interpreted as an exit signal.
        diagnostics.update({
            "positions_missing_market_data": 0,
            "positions_without_exit_signal": 0,
            "exit_risk_rejections": 0,
        })
        for identity, position in portfolio_positions.items():
            symbol = position["symbol"].upper()
            item = scan_by_identity.get(identity)
            if item is None:
                diagnostics["positions_missing_market_data"] += 1
                continue
            market = item.get("market", {})
            try:
                sell_price = float(market.get("bid") or market.get("last") or 0)
            except (TypeError, ValueError, OverflowError):
                sell_price = 0.0
            if not math.isfinite(sell_price) or sell_price <= 0:
                diagnostics["positions_missing_market_data"] += 1
                continue
            stop_loss = position.get("stop_loss")
            take_profit = position.get("take_profit")
            try:
                stop_value = float(stop_loss) if stop_loss is not None else None
                target_value = float(take_profit) if take_profit is not None else None
            except (TypeError, ValueError, OverflowError):
                stop_value = target_value = None
            if stop_value is not None and (not math.isfinite(stop_value) or stop_value <= 0):
                stop_value = None
            if target_value is not None and (not math.isfinite(target_value) or target_value <= 0):
                target_value = None

            exit_reason = None
            if stop_value is not None and sell_price <= stop_value:
                exit_reason = "persisted stop-loss triggered"
            elif target_value is not None and sell_price >= target_value:
                exit_reason = "persisted take-profit triggered"
            else:
                candidate = self._candidate_for(
                    item,
                    position.get("strategy_id"),
                    allow_experimental=self.allow_experimental_strategies,
                )
                # Only an explicit boolean False is a confirmed exit. None,
                # missing fields and malformed values fail closed (hold).
                if candidate is not None and candidate.get("current_hold_signal") is False:
                    exit_reason = (
                        "experimental strategy exit signal"
                        if str(position.get("strategy_id") or "").startswith("experimental:")
                        else "validated strategy exit signal"
                    )
            if exit_reason is None:
                diagnostics["positions_without_exit_signal"] += 1
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
            exit_evidence = {
                "market": market,
                "exit_reason": exit_reason,
                "observed_sell_price": sell_price,
                "position_quantity": positions[identity],
                "stop_loss": stop_value,
                "take_profit": target_value,
                "diagnostics": diagnostics,
            }
            if risk.accepted:
                return ChiefDecision(
                    action="sell",
                    symbol=symbol,
                    provider_id=proposal.provider_id,
                    instrument_id=proposal.instrument_id,
                    strategy_id=position.get("strategy_id"),
                    rationale=f"Position exit: {exit_reason}; Risk Manager approved",
                    evidence=exit_evidence,
                    proposal=proposal,
                    risk=risk,
                )
            diagnostics["exit_risk_rejections"] += 1
            return ChiefDecision(
                symbol=symbol,
                provider_id=proposal.provider_id,
                instrument_id=proposal.instrument_id,
                strategy_id=position.get("strategy_id"),
                rationale="Position exit proposal rejected by Risk Manager",
                evidence={**exit_evidence, "risk_rejection_reason": risk.reason},
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
            validated_candidates = (
                [
                    candidate for candidate in research.get("candidates", [])
                    if candidate.get("eligible")
                    and candidate.get("current_entry_signal")
                    and isinstance(candidate.get("score"), (int, float))
                    and math.isfinite(float(candidate["score"]))
                ]
                if research.get("recommended_strategy") else []
            )
            experimental = False
            if validated_candidates:
                candidate = max(validated_candidates, key=lambda row: float(row["score"]))
                strategy_id = candidate["strategy_id"]
                score = candidate["score"]
            elif self.allow_experimental_strategies:
                experimental_candidates = [
                    candidate for candidate in research.get("candidates", [])
                    if candidate.get("experimental_eligible")
                    and candidate.get("current_entry_signal")
                    and isinstance(candidate.get("experimental_score"), (int, float))
                    and math.isfinite(float(candidate["experimental_score"]))
                ]
                if not experimental_candidates:
                    continue
                candidate = max(
                    experimental_candidates,
                    key=lambda row: float(row["experimental_score"]),
                )
                strategy_id = candidate["strategy_id"]
                score = candidate["experimental_score"]
                experimental = True
            else:
                continue
            market = item.get("market", {})
            symbol = str(market.get("symbol", "")).upper()
            provider_id = str(market.get("provider_id") or "ccxt:kraken")
            identity = self._instrument_identity(provider_id, market.get("instrument_id"), symbol)
            if not symbol.endswith("/EUR"):
                diagnostics["excluded_non_eur_symbols"] += 1
                continue
            if identity in positions:
                diagnostics["excluded_open_positions"] += 1
                continue
            if not isinstance(score, (int, float)):
                continue
            if not (market.get("ask") or market.get("last")):
                diagnostics["excluded_missing_price"] += 1
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
                diagnostics["sniper_no_proposal"] += 1
                continue
            risk = self.risk_manager.evaluate(
                proposal,
                context,
                account_readiness=account_readiness,
                require_account_readiness=self.require_account_readiness,
            )
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
            diagnostics["risk_rejections"] += 1
            return ChiefDecision(
                symbol=symbol,
                provider_id=proposal.provider_id,
                instrument_id=proposal.instrument_id,
                strategy_id=strategy_id,
                rationale="Entry proposal rejected by Risk Manager",
                evidence={"score": score, "diagnostics": diagnostics, "risk_rejection_reason": risk.reason},
                proposal=proposal,
                risk=risk,
            )

        return ChiefDecision(
            rationale="No eligible strategy produced a valid entry or exit signal; hold cash and monitor",
            evidence={**diagnostics, "eligible_opportunities": len(opportunities)},
        )

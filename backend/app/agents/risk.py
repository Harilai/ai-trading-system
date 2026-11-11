import math

from .contracts import RiskContext, RiskDecision, TradeProposal


class RiskManager:
    """Deterministic risk gate for explicitly supported paper spot instruments."""

    def evaluate(
        self,
        proposal: TradeProposal,
        context: RiskContext,
        *,
        instrument_eligibility: dict | None = None,
    ) -> RiskDecision:
        # When an execution-readiness assessment is supplied, it is a mandatory
        # precondition. Missing, malformed, stale or non-supported evidence fails closed.
        if instrument_eligibility is not None:
            if not isinstance(instrument_eligibility, dict):
                return RiskDecision(accepted=False, reason="Instrument execution eligibility evidence is invalid")
            if instrument_eligibility.get("status") != "supported":
                return RiskDecision(accepted=False, reason="Instrument is not eligible for execution")
            if instrument_eligibility.get("provider_id") != proposal.provider_id:
                return RiskDecision(accepted=False, reason="Instrument eligibility provider identity mismatch")
            if proposal.instrument_id and instrument_eligibility.get("instrument_id") != proposal.instrument_id:
                return RiskDecision(accepted=False, reason="Instrument eligibility identity mismatch")
            if instrument_eligibility.get("execution_adapter_configured") is not True:
                return RiskDecision(accepted=False, reason="Execution adapter is not explicitly configured")
        symbol = proposal.symbol.strip().upper()
        numeric = (
            proposal.quantity,
            proposal.reference_price,
            context.equity_eur,
            context.fx_rate_to_eur,
            context.max_order_fraction,
            context.max_position_fraction,
            context.max_stop_distance_fraction,
            context.estimated_fee_rate,
            context.estimated_slippage_rate,
            context.available_cash_eur,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            return RiskDecision(accepted=False, reason="Risk inputs must be finite numbers")
        if (
            proposal.quantity <= 0 or proposal.reference_price <= 0
            or context.equity_eur <= 0 or context.fx_rate_to_eur <= 0
            or not 0 < context.max_order_fraction <= 1
            or not 0 < context.max_position_fraction <= 1
            or not 0 < context.max_stop_distance_fraction <= 1
            or not 0 <= context.estimated_fee_rate < 1
            or not 0 <= context.estimated_slippage_rate < 1
            or context.available_cash_eur < 0
        ):
            return RiskDecision(accepted=False, reason="Risk inputs are outside their permitted ranges")
        for protective_level in (proposal.stop_loss, proposal.take_profit):
            if protective_level is not None and (
                not math.isfinite(float(protective_level)) or float(protective_level) <= 0
            ):
                return RiskDecision(accepted=False, reason="Protective price levels must be positive finite numbers")

        if proposal.asset_class in {"equity", "etf"}:
            if proposal.market_type != "cash_equity":
                return RiskDecision(accepted=False, reason=f"Risk profile disabled: {proposal.asset_class}/{proposal.market_type}")
            if proposal.provider_id != "twelve_data" or context.quote_currency.upper() != "USD":
                return RiskDecision(accepted=False, reason="Only Twelve Data USD cash equities/ETFs are supported")
            if proposal.source_agent != "manual_owner":
                return RiskDecision(accepted=False, reason="Equity/ETF paper orders are manual-owner only in this phase")
            if proposal.side == "buy":
                if proposal.stop_loss is None:
                    return RiskDecision(accepted=False, reason="A buy proposal requires a stop-loss")
                if proposal.stop_loss >= proposal.reference_price:
                    return RiskDecision(accepted=False, reason="Buy stop-loss must be below the reference price")
                if proposal.take_profit is not None and proposal.take_profit <= proposal.reference_price:
                    return RiskDecision(accepted=False, reason="Buy take-profit must be above the reference price")
                stop_distance = (proposal.reference_price - proposal.stop_loss) / proposal.reference_price
                if stop_distance > context.max_stop_distance_fraction:
                    return RiskDecision(accepted=False, reason="Stop-loss distance exceeds the configured risk limit")
                notional_usd = proposal.quantity * proposal.reference_price
                estimated_total_usd = notional_usd * (1 + context.estimated_slippage_rate) * (1 + context.estimated_fee_rate)
                estimated_total_eur = estimated_total_usd * context.fx_rate_to_eur
                if not all(math.isfinite(value) and value > 0 for value in (notional_usd, estimated_total_usd, estimated_total_eur)):
                    return RiskDecision(accepted=False, reason="Calculated equity order notional is invalid")
                max_notional_eur = context.equity_eur * context.max_order_fraction
                available_usd = float(context.available_cash_by_currency.get("USD", 0.0))
                if not math.isfinite(available_usd) or available_usd < 0:
                    return RiskDecision(accepted=False, reason="USD cash balance is invalid")
                if estimated_total_eur > max_notional_eur:
                    return RiskDecision(accepted=False, reason="Order exceeds the maximum EUR portfolio allocation")
                held_quantity = float(context.positions.get(proposal.instrument_id or symbol, 0.0))
                current_position_eur = float(
                    context.position_values_eur.get(
                        proposal.instrument_id or symbol,
                        held_quantity * proposal.reference_price * context.fx_rate_to_eur,
                    )
                )
                max_position_eur = context.equity_eur * context.max_position_fraction
                if (
                    not math.isfinite(held_quantity) or held_quantity < 0
                    or not math.isfinite(current_position_eur) or current_position_eur < 0
                    or current_position_eur + estimated_total_eur > max_position_eur
                ):
                    return RiskDecision(accepted=False, reason="Order would exceed the maximum EUR instrument concentration")
                if estimated_total_usd > available_usd:
                    return RiskDecision(accepted=False, reason="Insufficient available USD cash including estimated fees")
                return RiskDecision(
                    accepted=True,
                    reason="Proposal passed deterministic manual USD cash-equity risk checks",
                    approved_quantity=proposal.quantity,
                    estimated_notional_eur=estimated_total_eur,
                )

            if proposal.side != "sell":
                return RiskDecision(accepted=False, reason="Unsupported equity order side")
            held_quantity = float(context.positions.get(proposal.instrument_id or symbol, 0.0))
            estimated_notional_eur = proposal.quantity * proposal.reference_price * context.fx_rate_to_eur
            if not math.isfinite(held_quantity) or held_quantity < 0 or not math.isfinite(estimated_notional_eur) or estimated_notional_eur <= 0:
                return RiskDecision(accepted=False, reason="Existing position or sell notional is invalid")
            if proposal.quantity > held_quantity:
                return RiskDecision(accepted=False, reason="Sell quantity exceeds the existing position; shorting is disabled")
            return RiskDecision(
                accepted=True,
                reason="Sell proposal only reduces an existing USD cash-equity position",
                approved_quantity=proposal.quantity,
                estimated_notional_eur=estimated_notional_eur,
            )

        if proposal.market_type != "spot" or proposal.asset_class != "crypto":
            return RiskDecision(accepted=False, reason=f"Risk profile disabled: {proposal.asset_class}/{proposal.market_type}")
        if context.quote_currency.upper() != "EUR" or not symbol.endswith("/EUR"):
            return RiskDecision(accepted=False, reason="Only EUR-quoted crypto spot markets are allowed")

        if proposal.side == "buy":
            if proposal.stop_loss is None:
                return RiskDecision(accepted=False, reason="A buy proposal requires a stop-loss")
            if proposal.stop_loss >= proposal.reference_price:
                return RiskDecision(accepted=False, reason="Buy stop-loss must be below the reference price")
            if proposal.take_profit is not None and proposal.take_profit <= proposal.reference_price:
                return RiskDecision(accepted=False, reason="Buy take-profit must be above the reference price")
            stop_distance = (proposal.reference_price - proposal.stop_loss) / proposal.reference_price
            if stop_distance > context.max_stop_distance_fraction:
                return RiskDecision(accepted=False, reason="Stop-loss distance exceeds the configured risk limit")

            notional = proposal.quantity * proposal.reference_price
            estimated_total = notional * (1 + context.estimated_fee_rate)
            if not all(math.isfinite(value) and value > 0 for value in (notional, estimated_total)):
                return RiskDecision(accepted=False, reason="Calculated spot order notional is invalid")
            max_notional = context.equity_eur * context.max_order_fraction
            if estimated_total > max_notional:
                return RiskDecision(accepted=False, reason="Order exceeds the maximum portfolio allocation")
            if estimated_total > context.available_cash_eur:
                return RiskDecision(accepted=False, reason="Insufficient available cash including estimated fees")
            return RiskDecision(
                accepted=True,
                reason="Proposal passed deterministic spot-risk checks",
                approved_quantity=proposal.quantity,
                estimated_notional_eur=estimated_total,
            )

        held_quantity = float(context.positions.get(proposal.instrument_id or symbol, context.positions.get(symbol, 0.0)))
        estimated_notional_eur = proposal.quantity * proposal.reference_price
        if not math.isfinite(held_quantity) or held_quantity < 0 or not math.isfinite(estimated_notional_eur) or estimated_notional_eur <= 0:
            return RiskDecision(accepted=False, reason="Existing position or sell notional is invalid")
        if proposal.quantity > held_quantity:
            return RiskDecision(accepted=False, reason="Sell quantity exceeds the existing position; shorting is disabled")
        return RiskDecision(
            accepted=True,
            reason="Sell proposal only reduces an existing spot position",
            approved_quantity=proposal.quantity,
            estimated_notional_eur=estimated_notional_eur,
        )

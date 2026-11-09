from .contracts import RiskContext, RiskDecision, TradeProposal


class RiskManager:
    """Deterministic spot-market gate. It cannot change its own risk limits."""

    def evaluate(self, proposal: TradeProposal, context: RiskContext) -> RiskDecision:
        symbol = proposal.symbol.strip().upper()
        if proposal.market_type != "spot" or proposal.asset_class != "crypto":
            return RiskDecision(accepted=False, reason=f"Risk profile disabled: {proposal.asset_class}/{proposal.market_type}")
        if not symbol.endswith("/EUR"):
            return RiskDecision(accepted=False, reason="Only EUR-quoted spot markets are allowed")

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

        # Spot-only phase: a sell can only reduce an existing long position.
        held_quantity = context.positions.get(proposal.instrument_id or symbol, context.positions.get(symbol, 0.0))
        if proposal.quantity > held_quantity:
            return RiskDecision(accepted=False, reason="Sell quantity exceeds the existing position; shorting is disabled")
        return RiskDecision(
            accepted=True,
            reason="Sell proposal only reduces an existing spot position",
            approved_quantity=proposal.quantity,
            estimated_notional_eur=proposal.quantity * proposal.reference_price,
        )

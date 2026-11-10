import hmac
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .config import settings
from .indicators import atr, bollinger_bands, ema, macd, rsi, sma
from .market_data import MarketDataError
from .providers.ccxt_spot import CcxtSpotProvider
from .providers.twelve_data import TwelveDataProvider
from .providers.base import AssetClass
from .equity_research import EquityEtfResearchAgent
from .providers.registry import ProviderRegistry
from .scout import ScoutAgent
from .strategy_research import StrategyResearchAgent
from .chief import ChiefAgent
from .agents.contracts import RiskContext, TradeProposal
from .agents.risk import RiskManager
from .portfolio import PaperPortfolio, PortfolioError
from .fx import invert_fx_observation, validate_fx_observation

WORKER_HEARTBEAT_MAX_AGE_SECONDS = 600

app = FastAPI(title="AI Trading System", version="0.1.0", description="Paper trading only; live execution is not implemented.")
market = ProviderRegistry()
for configured_provider in settings.market_data_provider_ids.split(','):
    provider_id = configured_provider.strip().lower()
    if provider_id:
        market.register(CcxtSpotProvider(provider_id))
if settings.twelve_data_api_key:
    market.register(TwelveDataProvider(
        settings.twelve_data_api_key,
        state_path=settings.twelve_data_state_path,
        daily_credit_budget=settings.twelve_data_daily_credit_budget,
        min_request_interval_seconds=settings.twelve_data_min_request_interval_seconds,
    ))
portfolio = PaperPortfolio(settings.database_path, settings.initial_cash_eur)

class OrderRequest(BaseModel):
    symbol: str = Field(min_length=3)
    provider_id: str = "ccxt:kraken"
    instrument_id: str | None = None
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    strategy_id: str | None = None
    client_order_id: str | None = Field(default=None, min_length=1, max_length=128)


class FxConversionRequest(BaseModel):
    from_currency: Literal["EUR", "USD"]
    to_currency: Literal["EUR", "USD"]
    amount: float = Field(gt=0)
    client_conversion_id: str = Field(min_length=1, max_length=128)

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    dashboard_file = Path(__file__).with_name("dashboard.html")
    return FileResponse(dashboard_file, media_type="text/html")


@app.get("/api/providers")
def providers():
    return market.describe()


@app.get("/api/risk-profiles")
def risk_profiles():
    from .risk_profiles import RISK_PROFILES
    return {
        name.value: {**profile, "controls": list(profile["controls"])}
        for name, profile in RISK_PROFILES.items()
    }


@app.get("/api/markets")
def markets():
    instruments, failures = market.discover_markets()
    return {
        "discovered": len(instruments),
        "instruments": [
            {
                "instrument_id": item.instrument_id,
                "provider_id": item.provider_id,
                "symbol": item.symbol,
                "asset_class": item.asset_class.value,
                "market_type": item.market_type.value,
                "base": item.base,
                "quote": item.quote,
                "active": item.active,
                "minimum_amount": item.min_amount,
                "minimum_cost": item.min_cost,
                "maker_fee": item.maker_fee,
                "taker_fee": item.taker_fee,
            }
            for item in instruments
        ],
        "provider_failures": failures,
    }




@app.get("/api/research/equities")
def research_equities(
    max_instruments: int = Query(5, ge=1, le=10),
    max_quote_candidates: int = Query(10, ge=1, le=100),
    timeframe: str = Query("1day", pattern="^(1day|1h|4h)$"),
):
    if max_quote_candidates < max_instruments:
        raise HTTPException(422, "max_quote_candidates must be greater than or equal to max_instruments")
    try:
        provider = market.get("twelve_data")
        agent = EquityEtfResearchAgent(
            provider,
            max_instruments=max_instruments,
            max_quote_candidates=max_quote_candidates,
            timeframe=timeframe,
            rotation_state_path=settings.database_path,
        )
        return agent.scan()
    except MarketDataError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "paper", "live_trading_enabled": False}


@app.get("/api/health/ready")
def readiness():
    try:
        with agent_store.connect() as db:
            db.execute("SELECT 1")
        state = agent_store.state()
        heartbeat = state.get("last_heartbeat")
        if not heartbeat:
            raise HTTPException(503, "Autonomous worker has not reported a heartbeat")
        heartbeat_at = datetime.fromisoformat(heartbeat)
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
        if age_seconds < 0 or age_seconds > WORKER_HEARTBEAT_MAX_AGE_SECONDS:
            raise HTTPException(503, "Autonomous worker heartbeat is stale")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Database or worker readiness check failed") from exc

    return {
        "status": "ready",
        "mode": "paper",
        "database": "ok",
        "worker": "ok",
        "worker_heartbeat_age_seconds": round(age_seconds, 2),
        "live_trading_enabled": False,
    }

@app.get("/api/market/{symbol:path}/ticker")
def ticker(symbol: str, provider_id: str | None = None):
    try: return market.ticker(symbol.upper(), provider_id)
    except MarketDataError as exc: raise HTTPException(502,str(exc)) from exc

@app.get("/api/market/{symbol:path}/ohlcv")
def candles(symbol: str, timeframe: str="1h", limit: int=Query(100,ge=30,le=500), provider_id: str | None = None):
    try:
        rows=market.ohlcv(symbol.upper(),timeframe,limit,provider_id=provider_id)
        return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":[{"timestamp":r[0],"open":r[1],"high":r[2],"low":r[3],"close":r[4],"volume":r[5]} for r in rows]}
    except MarketDataError as exc: raise HTTPException(502,str(exc)) from exc

@app.get("/api/market/{symbol:path}/indicators")
def indicators(symbol: str, timeframe: str="1h", limit: int=Query(100,ge=50,le=500), provider_id: str | None = None):
    try:
        rows=market.ohlcv(symbol.upper(),timeframe,limit,provider_id=provider_id)
        closes=[r[4] for r in rows]
        bars=[{"high":r[2],"low":r[3],"close":r[4]} for r in rows]
        return {"symbol":symbol.upper(),"timeframe":timeframe,"as_of_ms":rows[-1][0],
                "ema_20":ema(closes,20),"ema_50":ema(closes,50),"sma_20":sma(closes,20),
                "rsi_14":rsi(closes,14),"macd":macd(closes),"bollinger_20":bollinger_bands(closes,20),"atr_14":atr(bars,14)}
    except (MarketDataError,ValueError) as exc: raise HTTPException(502,str(exc)) from exc

def _load_fx_observation(symbol: str) -> dict:
    normalized = str(symbol).strip().upper()
    if normalized not in {"EUR/USD", "USD/EUR"}:
        raise MarketDataError("Only EUR/USD FX observations are currently supported")
    provider = market.get("twelve_data")
    observation = validate_fx_observation(
        provider.exchange_rate("EUR/USD"),
        "EUR/USD",
        max_age_seconds=settings.max_fx_data_age_seconds,
    )
    return observation if normalized == "EUR/USD" else invert_fx_observation(observation)


@app.get("/api/fx/{symbol:path}")
def fx_rate(symbol: str):
    try:
        return _load_fx_observation(symbol)
    except MarketDataError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/portfolio")
def get_portfolio():
    base = portfolio.snapshot()
    prices = {}
    for position in base["positions"]:
        try:
            provider_id = position.get("provider_id", "ccxt:kraken")
            instrument_id = position.get("instrument_id") or f"{provider_id}:spot:{position['symbol']}"
            quote = market.ticker(position["symbol"], provider_id)
            expected_currency = str(position.get("quote_currency") or "EUR").upper()
            if quote.get("symbol") and str(quote["symbol"]).upper() != str(position["symbol"]).upper():
                continue
            if quote.get("currency") and str(quote["currency"]).upper() != expected_currency:
                continue
            if quote.get("last") is not None and not market.is_stale(quote, settings.max_data_age_seconds):
                prices[instrument_id] = float(quote["last"])
        except (MarketDataError, TypeError, ValueError, OverflowError):
            continue

    has_usd = any(
        item.get("currency") == "USD" and abs(float(item.get("balance", 0))) > 1e-12
        for item in base.get("cash_balances", [])
    )
    has_usd = has_usd or any(str(item.get("quote_currency") or "EUR").upper() == "USD" for item in base["positions"])
    fx_rates = {}
    if has_usd:
        try:
            fx_rates["USD"] = _load_fx_observation("USD/EUR")
        except MarketDataError:
            # Snapshot remains explicitly incomplete; never substitute a guessed FX rate.
            pass
    return portfolio.snapshot(prices, fx_rates)

@app.get("/api/trades")
def trades(limit: int=Query(50,ge=1,le=200)): return portfolio.history(limit)

def require_order_control_token(token: str | None) -> None:
    if not settings.agent_control_token:
        raise HTTPException(503, "Paper order API disabled: configure AGENT_CONTROL_TOKEN")
    if token is None or not hmac.compare_digest(token, settings.agent_control_token):
        raise HTTPException(401, "Invalid paper order control token")


@app.get("/api/agent/fx-conversions")
def fx_conversion_history(
    limit: int = Query(50, ge=1, le=200),
    x_agent_token: str | None = Header(default=None),
):
    require_order_control_token(x_agent_token)
    return {"items": portfolio.conversion_history(limit)}


@app.post("/api/agent/fx-conversions", status_code=201)
def paper_fx_conversion(
    request: FxConversionRequest,
    x_agent_token: str | None = Header(default=None),
):
    require_order_control_token(x_agent_token)
    conversion_key = request.client_conversion_id.strip()
    if not conversion_key:
        raise HTTPException(422, "client_conversion_id must not be blank")
    existing = portfolio.get_conversion_by_client_conversion_id(conversion_key)
    if existing:
        same_intent = (
            existing["from_currency"] == request.from_currency
            and existing["to_currency"] == request.to_currency
            and math.isclose(float(existing["from_amount"]), request.amount, rel_tol=1e-12, abs_tol=1e-12)
        )
        if not same_intent:
            raise HTTPException(409, "client_conversion_id was already used for a different conversion")
        existing["idempotent_replay"] = True
        return existing
    if request.from_currency == request.to_currency:
        raise HTTPException(422, "Source and target currencies must differ")
    try:
        pair = f"{request.from_currency}/{request.to_currency}"
        observation = _load_fx_observation(pair)
        return portfolio.convert_currency(
            request.from_currency,
            request.to_currency,
            request.amount,
            observation["rate"],
            settings.fx_conversion_fee_rate,
            conversion_key,
            observation["provider_id"],
            observation["timestamp"],
        )
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(503, str(exc)) from exc


def _paper_equity_order(order: OrderRequest):
    if not order.client_order_id or not order.client_order_id.strip():
        raise HTTPException(422, "USD equity/ETF paper orders require client_order_id for idempotency")
    previous = portfolio.get_trade_by_client_order_id(order.client_order_id.strip())
    if previous:
        portfolio.reconcile_order_intents(
            active_task_ids=agent_store.active_task_ids(),
            max_data_age_seconds=settings.max_data_age_seconds,
            max_fx_age_seconds=settings.max_fx_data_age_seconds,
        )
        existing_intent = portfolio.get_order_intent(order.client_order_id.strip())
        if existing_intent and existing_intent["status"] == "reconciliation_required":
            raise HTTPException(503, "Paper order requires reconciliation; replay is blocked")
        if existing_intent and existing_intent["status"] != "filled":
            raise HTTPException(503, "Trade ledger and order intent are not reconciled")
        same_intent = (
            previous["symbol"] == order.symbol.strip().upper()
            and previous["side"] == order.side
            and previous["provider_id"] == "twelve_data"
            and previous["quote_currency"] == "USD"
            and previous.get("strategy_id") == order.strategy_id
            and (not order.instrument_id or previous["instrument_id"] == order.instrument_id)
            and (
                (previous.get("stop_loss") is None and order.stop_loss is None)
                or (
                    previous.get("stop_loss") is not None and order.stop_loss is not None
                    and math.isclose(float(previous["stop_loss"]), order.stop_loss, rel_tol=1e-12, abs_tol=1e-12)
                )
            )
            and (
                (previous.get("take_profit") is None and order.take_profit is None)
                or (
                    previous.get("take_profit") is not None and order.take_profit is not None
                    and math.isclose(float(previous["take_profit"]), order.take_profit, rel_tol=1e-12, abs_tol=1e-12)
                )
            )
            and math.isclose(float(previous["quantity"]), order.quantity, rel_tol=1e-12, abs_tol=1e-12)
        )
        if not same_intent:
            raise HTTPException(409, "client_order_id was already used for a different paper order")
        previous["mode"] = "paper"
        previous["idempotent_replay"] = True
        return previous

    provider = market.get("twelve_data")
    instruments = provider.discover_markets((AssetClass.EQUITY, AssetClass.ETF))
    matches = [
        item for item in instruments
        if item.symbol.upper() == order.symbol.strip().upper()
    ]
    if len(matches) != 1:
        raise HTTPException(422, "Twelve Data symbol is missing or ambiguous; refusing an uncertain instrument identity")
    instrument = matches[0]
    if not instrument.active:
        raise HTTPException(422, "Instrument is not an active Twelve Data US equity/ETF")
    if instrument.quote != "USD" or instrument.market_type.value != "cash_equity":
        raise HTTPException(422, "Only USD cash equities and ETFs are supported")
    if order.instrument_id and order.instrument_id != instrument.instrument_id:
        raise HTTPException(422, "Instrument identity does not match Twelve Data discovery")

    quote = provider.ticker(instrument.symbol)
    try:
        last = float(quote["last"])
        quote_timestamp = int(quote["timestamp"])
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if (
            not math.isfinite(last) or last <= 0
            or quote_timestamp <= 0
            or quote_timestamp > now_ms
            or now_ms - quote_timestamp > settings.max_data_age_seconds * 1000
        ):
            raise ValueError("invalid or stale quote")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(503, "Missing, malformed or stale equity market data; order rejected") from exc
    if quote.get("symbol") and str(quote["symbol"]).upper() != instrument.symbol.upper():
        raise HTTPException(503, "Equity quote symbol does not match the discovered instrument")
    if quote.get("currency") and str(quote["currency"]).upper() != "USD":
        raise HTTPException(503, "Equity quote currency does not match the USD instrument")
    expected_exchange = instrument.metadata.get("exchange")
    expected_mic = instrument.metadata.get("mic_code")
    if expected_exchange and quote.get("exchange") and str(quote["exchange"]).casefold() != str(expected_exchange).casefold():
        raise HTTPException(503, "Equity quote exchange does not match the discovered instrument")
    if expected_mic and quote.get("mic_code") and str(quote["mic_code"]).casefold() != str(expected_mic).casefold():
        raise HTTPException(503, "Equity quote MIC does not match the discovered instrument")
    bid = ask = None
    try:
        if quote.get("bid") is not None and quote.get("ask") is not None:
            bid, ask = float(quote["bid"]), float(quote["ask"])
            if not all(math.isfinite(value) and value > 0 for value in (bid, ask)) or ask < bid:
                raise ValueError("invalid bid/ask")
            midpoint = (bid + ask) / 2
            spread_fraction = (ask - bid) / midpoint
            if midpoint <= 0 or spread_fraction > settings.max_equity_spread_fraction:
                raise ValueError("bid/ask spread exceeds the configured limit")
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(503, "Equity bid/ask quote is malformed or its spread is too wide") from exc

    market_open = quote.get("is_market_open")
    if market_open is None:
        try:
            states = provider.market_state()
            keys = (
                instrument.metadata.get("exchange"),
                instrument.metadata.get("mic_code"),
            )
            state = next(
                (states.get(str(key).casefold()) for key in keys if key and states.get(str(key).casefold())),
                None,
            )
            market_open = state.get("is_market_open") if state else None
        except MarketDataError:
            market_open = None
    if market_open is not True:
        raise HTTPException(503, "US exchange session is closed or unknown; equity order rejected")

    fx = _load_fx_observation("USD/EUR")
    snapshot = get_portfolio()
    if not snapshot["valuation_complete"]:
        raise HTTPException(503, "Portfolio valuation is incomplete; equity order rejected")
    cash_by_currency = {
        item["currency"]: float(item["balance"])
        for item in snapshot.get("cash_balances", [])
    }
    positions = {
        item["instrument_id"]: float(item["quantity"])
        for item in snapshot["positions"]
        if item.get("instrument_id")
    }
    position_values_eur = {
        item["instrument_id"]: float(item["market_value_eur"])
        for item in snapshot["positions"]
        if item.get("instrument_id") and item.get("market_value_eur") is not None
    }
    # Twelve Data's REST quote often exposes last price but not bid/ask for equities.
    # When both sides exist use the executable side; otherwise use last plus configured paper slippage.
    reference_price = (ask if order.side == "buy" else bid) if bid is not None and ask is not None else last
    context = RiskContext(
        equity_eur=float(snapshot["equity"]),
        available_cash_eur=float(snapshot["available_cash_for_manual_withdrawal"]),
        available_cash_by_currency=cash_by_currency,
        quote_currency="USD",
        fx_rate_to_eur=float(fx["rate"]),
        max_order_fraction=settings.max_equity_order_fraction,
        max_position_fraction=settings.max_equity_position_fraction,
        max_stop_distance_fraction=settings.max_equity_stop_distance_fraction,
        estimated_fee_rate=settings.equity_paper_fee_rate,
        estimated_slippage_rate=settings.equity_paper_slippage_rate,
        positions=positions,
        position_values_eur=position_values_eur,
    )
    proposal = TradeProposal(
        symbol=instrument.symbol,
        provider_id=instrument.provider_id,
        instrument_id=instrument.instrument_id,
        asset_class=instrument.asset_class.value,
        market_type=instrument.market_type.value,
        side=order.side,
        quantity=order.quantity,
        reference_price=reference_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        rationale="Owner-submitted USD cash-equity/ETF paper order evaluated by deterministic Risk Manager",
        source_agent="manual_owner",
    )
    risk = RiskManager().evaluate(proposal, context)
    if not risk.accepted:
        raise HTTPException(422, f"Risk Manager rejected order: {risk.reason}")

    execution_request = {
        "symbol": instrument.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "price": reference_price,
        "fee_rate": settings.equity_paper_fee_rate,
        "slippage_rate": settings.equity_paper_slippage_rate,
        "max_fraction": 1.0,
        "minimum": max(settings.min_order_value_usd, float(instrument.min_cost or 0)),
        "client_order_id": order.client_order_id.strip(),
        "provider_id": instrument.provider_id,
        "instrument_id": instrument.instrument_id,
        "strategy_id": order.strategy_id,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "quote_currency": "USD",
        "fx_rate_to_eur": float(fx["rate"]),
        "fx_provider_id": fx["provider_id"],
        "fx_timestamp": fx["timestamp"],
        "market_timestamp": quote_timestamp,
        "risk_fx_rate_to_eur": float(fx["rate"]),
        "risk_fx_provider_id": fx["provider_id"],
        "risk_fx_timestamp": fx["timestamp"],
    }
    portfolio.reconcile_order_intents(
        active_task_ids=agent_store.active_task_ids(),
        max_data_age_seconds=settings.max_data_age_seconds,
        max_fx_age_seconds=settings.max_fx_data_age_seconds,
    )
    try:
        portfolio.record_order_intent(
            order.client_order_id.strip(),
            execution_request,
            {"risk": risk.model_dump(), "instrument_id": instrument.instrument_id, "provider_id": instrument.provider_id},
        )
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not portfolio.order_intent_is_fresh(
        order.client_order_id.strip(),
        settings.max_data_age_seconds,
        settings.max_fx_data_age_seconds,
    ):
        portfolio.expire_order_intent(
            order.client_order_id.strip(),
            "Market/FX evidence expired before paper fill",
        )
        raise HTTPException(503, "Market or FX evidence expired before paper fill")
    try:
        return portfolio.execute(**execution_request, require_order_intent=True)
    except PortfolioError as exc:
        current_intent = portfolio.get_order_intent(order.client_order_id.strip())
        if current_intent and current_intent["status"] == "risk_approved":
            portfolio.reject_order_intent(order.client_order_id.strip(), str(exc))
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/orders", status_code=201)
def paper_order(
    order: OrderRequest,
    x_agent_token: str | None = Header(default=None),
):
    symbol = order.symbol.strip().upper()
    if order.side == "buy" and order.stop_loss is None:
        raise HTTPException(422, "A buy order requires an explicit stop_loss")
    require_order_control_token(x_agent_token)
    if not order.client_order_id or not order.client_order_id.strip():
        raise HTTPException(422, "Manual paper orders require client_order_id for idempotency")

    if order.provider_id == "twelve_data":
        try:
            return _paper_equity_order(order)
        except PortfolioError as exc:
            raise HTTPException(422, str(exc)) from exc
        except MarketDataError as exc:
            raise HTTPException(503, str(exc)) from exc

    if not symbol.endswith("/EUR"):
        raise HTTPException(
            422,
            "Only EUR-quoted crypto spot or supported Twelve Data USD cash equities/ETFs are enabled",
        )

    try:
        previous = portfolio.get_trade_by_client_order_id(order.client_order_id.strip())
        if previous:
            portfolio.reconcile_order_intents(
            active_task_ids=agent_store.active_task_ids(),
            max_data_age_seconds=settings.max_data_age_seconds,
            max_fx_age_seconds=settings.max_fx_data_age_seconds,
        )
            existing_intent = portfolio.get_order_intent(order.client_order_id.strip())
            if existing_intent and existing_intent["status"] == "reconciliation_required":
                raise HTTPException(503, "Paper order requires reconciliation; replay is blocked")
            if existing_intent and existing_intent["status"] != "filled":
                raise HTTPException(503, "Trade ledger and order intent are not reconciled")
            same_intent = (
                previous["symbol"] == symbol
                and previous["side"] == order.side
                and previous["provider_id"] == order.provider_id
                and previous["quote_currency"] == "EUR"
                and previous.get("strategy_id") == order.strategy_id
                and math.isclose(float(previous["quantity"]), order.quantity, rel_tol=1e-12, abs_tol=1e-12)
                and previous.get("stop_loss") == order.stop_loss
                and previous.get("take_profit") == order.take_profit
                and (not order.instrument_id or previous["instrument_id"] == order.instrument_id)
            )
            if not same_intent:
                raise HTTPException(409, "client_order_id was already used for a different paper order")
            previous["mode"] = "paper"
            previous["idempotent_replay"] = True
            return previous
        quote = market.ticker(symbol, order.provider_id)
        if quote["last"] is None or market.is_stale(quote, settings.max_data_age_seconds):
            raise HTTPException(503, "Missing or stale market data; order rejected")
        reference_price = float(quote["last"])
        quote_timestamp = int(quote["timestamp"])
        instrument_id = order.instrument_id or f"{order.provider_id}:spot:{symbol}"

        snapshot = get_portfolio()
        if not snapshot["valuation_complete"]:
            raise HTTPException(503, "Portfolio valuation is incomplete; paper order rejected")
        positions = {
            position["instrument_id"]: float(position["quantity"])
            for position in snapshot["positions"]
            if position.get("instrument_id")
        }
        context = RiskContext(
            equity_eur=float(snapshot["equity"]),
            available_cash_eur=float(snapshot["available_cash_for_manual_withdrawal"]),
            max_order_fraction=settings.max_order_fraction,
            max_stop_distance_fraction=settings.max_stop_distance_fraction,
            estimated_fee_rate=settings.fee_rate,
            quote_currency="EUR",
            positions=positions,
        )
        proposal = TradeProposal(
            symbol=symbol,
            provider_id=order.provider_id,
            instrument_id=instrument_id,
            side=order.side,
            quantity=order.quantity,
            reference_price=reference_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            rationale="Owner-submitted paper order evaluated by deterministic Risk Manager",
            source_agent="manual_owner",
        )
        risk = RiskManager().evaluate(proposal, context)
        if not risk.accepted:
            raise HTTPException(422, f"Risk Manager rejected order: {risk.reason}")
        risk_fx = (snapshot.get("fx_observations") or {}).get("USD") or {}
        execution_request = {
            "symbol": symbol,
            "side": order.side,
            "quantity": risk.approved_quantity,
            "price": reference_price,
            "fee_rate": settings.fee_rate,
            "slippage_rate": settings.slippage_rate,
            "max_fraction": settings.max_order_fraction,
            "minimum": settings.min_order_value_eur,
            "client_order_id": order.client_order_id.strip(),
            "provider_id": order.provider_id,
            "instrument_id": instrument_id,
            "strategy_id": order.strategy_id,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "quote_currency": "EUR",
            "fx_rate_to_eur": 1.0,
            "fx_provider_id": None,
            "fx_timestamp": None,
            "market_timestamp": quote_timestamp,
            "risk_fx_rate_to_eur": float(risk_fx.get("rate", 1.0)),
            "risk_fx_provider_id": risk_fx.get("provider_id"),
            "risk_fx_timestamp": risk_fx.get("timestamp"),
        }
        portfolio.reconcile_order_intents(
            active_task_ids=agent_store.active_task_ids(),
            max_data_age_seconds=settings.max_data_age_seconds,
            max_fx_age_seconds=settings.max_fx_data_age_seconds,
        )
        portfolio.record_order_intent(
            order.client_order_id.strip(),
            execution_request,
            {"risk": risk.model_dump(), "proposal": proposal.model_dump()},
        )
        if not portfolio.order_intent_is_fresh(
            order.client_order_id.strip(),
            settings.max_data_age_seconds,
            settings.max_fx_data_age_seconds,
        ):
            portfolio.expire_order_intent(
                order.client_order_id.strip(),
                "Market/FX evidence expired before paper fill",
            )
            raise HTTPException(503, "Market or FX evidence expired before paper fill")
        try:
            return portfolio.execute(**execution_request, require_order_intent=True)
        except PortfolioError as exc:
            current_intent = portfolio.get_order_intent(order.client_order_id.strip())
            if current_intent and current_intent["status"] == "risk_approved":
                portfolio.reject_order_intent(order.client_order_id.strip(), str(exc))
            raise
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(502, str(exc)) from exc


from contextlib import asynccontextmanager
from .autonomous import AutonomousAgent, AutonomousStore
from .agent_api import router as agent_router

agent_store = AutonomousStore(settings.database_path)
scout = ScoutAgent(
    market,
    strategy_research=StrategyResearchAgent(
        fee_rate=settings.fee_rate,
        slippage_rate=settings.slippage_rate,
        max_position_fraction=settings.max_order_fraction,
    ),
    max_ticker_age_seconds=settings.max_data_age_seconds,
)
chief = ChiefAgent(
    max_order_fraction=settings.max_order_fraction,
    estimated_fee_rate=settings.fee_rate,
    estimated_slippage_rate=settings.slippage_rate,
    minimum_order_value_eur=settings.min_order_value_eur,
)
equity_research_agent = (
    EquityEtfResearchAgent(
        market.get("twelve_data"),
        max_instruments=5,
        max_quote_candidates=10,
        rotation_state_path=settings.database_path,
    )
    if settings.twelve_data_api_key else None
)
agent_worker = AutonomousAgent(
    agent_store,
    scout=scout,
    chief=chief,
    portfolio=portfolio,
    equity_research=equity_research_agent,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent_worker.start()
    yield
    await agent_worker.stop()

app.router.lifespan_context = lifespan
app.include_router(agent_router)

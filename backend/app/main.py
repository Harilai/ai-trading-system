import hmac
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from .config import settings
from .indicators import atr, bollinger_bands, ema, macd, rsi, sma
from .market_data import MarketDataError
from .providers.ccxt_spot import CcxtSpotProvider
from .providers.twelve_data import TwelveDataProvider
from .equity_research import EquityEtfResearchAgent
from .providers.registry import ProviderRegistry
from .scout import ScoutAgent
from .strategy_research import StrategyResearchAgent
from .chief import ChiefAgent
from .agents.contracts import RiskContext, TradeProposal
from .agents.risk import RiskManager
from .portfolio import PaperPortfolio, PortfolioError

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
        if age_seconds < 0 or age_seconds > 30:
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

@app.get("/api/portfolio")
def get_portfolio():
    base = portfolio.snapshot()
    prices = {}
    for position in base["positions"]:
        try:
            provider_id = position.get("provider_id", "ccxt:kraken")
            instrument_id = position.get("instrument_id") or f"{provider_id}:spot:{position['symbol']}"
            quote = market.ticker(position["symbol"], provider_id)
            if quote["last"] is not None and not market.is_stale(quote, settings.max_data_age_seconds):
                prices[instrument_id] = float(quote["last"])
        except MarketDataError:
            continue
    return portfolio.snapshot(prices)

@app.get("/api/trades")
def trades(limit: int=Query(50,ge=1,le=200)): return portfolio.history(limit)

def require_order_control_token(token: str | None) -> None:
    if not settings.agent_control_token:
        raise HTTPException(503, "Paper order API disabled: configure AGENT_CONTROL_TOKEN")
    if token is None or not hmac.compare_digest(token, settings.agent_control_token):
        raise HTTPException(401, "Invalid paper order control token")


@app.post("/api/orders", status_code=201)
def paper_order(
    order: OrderRequest,
    x_agent_token: str | None = Header(default=None),
):
    symbol = order.symbol.strip().upper()
    if not symbol.endswith("/EUR"):
        raise HTTPException(
            422,
            "Only EUR-quoted pairs (for example BTC/EUR) are enabled in Sprint 1",
        )
    if order.side == "buy" and order.stop_loss is None:
        raise HTTPException(422, "A buy order requires an explicit stop_loss")
    require_order_control_token(x_agent_token)

    try:
        quote = market.ticker(symbol, order.provider_id)
        if quote["last"] is None or market.is_stale(quote, settings.max_data_age_seconds):
            raise HTTPException(503, "Missing or stale market data; order rejected")
        reference_price = float(quote["last"])
        instrument_id = order.instrument_id or f"{order.provider_id}:spot:{symbol}"

        snapshot = portfolio.snapshot()
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

        return portfolio.execute(
            symbol,
            order.side,
            order.quantity,
            reference_price,
            settings.fee_rate,
            settings.slippage_rate,
            settings.max_order_fraction,
            settings.min_order_value_eur,
            provider_id=order.provider_id,
            instrument_id=instrument_id,
            strategy_id=order.strategy_id,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )
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

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from .config import settings
from .indicators import atr, bollinger_bands, ema, macd, rsi, sma
from .market_data import CcxtMarketData, MarketDataError
from .portfolio import PaperPortfolio, PortfolioError

app = FastAPI(title="AI Trading System", version="0.1.0", description="Paper trading only; live execution is not implemented.")
market = CcxtMarketData(settings.exchange_id)
portfolio = PaperPortfolio(settings.database_path, settings.initial_cash_eur)

class OrderRequest(BaseModel):
    symbol: str = Field(min_length=3)
    side: str
    quantity: float = Field(gt=0)

@app.get("/api/health")
def health():
    return {"status":"ok","mode":"paper","live_trading_enabled":False}

@app.get("/api/market/{symbol:path}/ticker")
def ticker(symbol: str):
    try: return market.ticker(symbol.upper())
    except MarketDataError as exc: raise HTTPException(502,str(exc)) from exc

@app.get("/api/market/{symbol:path}/ohlcv")
def candles(symbol: str, timeframe: str="1h", limit: int=Query(100,ge=30,le=500)):
    try:
        rows=market.ohlcv(symbol.upper(),timeframe,limit)
        return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":[{"timestamp":r[0],"open":r[1],"high":r[2],"low":r[3],"close":r[4],"volume":r[5]} for r in rows]}
    except MarketDataError as exc: raise HTTPException(502,str(exc)) from exc

@app.get("/api/market/{symbol:path}/indicators")
def indicators(symbol: str, timeframe: str="1h", limit: int=Query(100,ge=50,le=500)):
    try:
        rows=market.ohlcv(symbol.upper(),timeframe,limit)
        closes=[r[4] for r in rows]
        bars=[{"high":r[2],"low":r[3],"close":r[4]} for r in rows]
        return {"symbol":symbol.upper(),"timeframe":timeframe,"as_of_ms":rows[-1][0],
                "ema_20":ema(closes,20),"ema_50":ema(closes,50),"sma_20":sma(closes,20),
                "rsi_14":rsi(closes,14),"macd":macd(closes),"bollinger_20":bollinger_bands(closes,20),"atr_14":atr(bars,14)}
    except (MarketDataError,ValueError) as exc: raise HTTPException(502,str(exc)) from exc

@app.get("/api/portfolio")
def get_portfolio(): return portfolio.snapshot()

@app.get("/api/trades")
def trades(limit: int=Query(50,ge=1,le=200)): return portfolio.history(limit)

@app.post("/api/orders",status_code=201)
def paper_order(order: OrderRequest):
    symbol=order.symbol.strip().upper()
    if not symbol.endswith("/EUR"): raise HTTPException(422,"Only EUR-quoted pairs (for example BTC/EUR) are enabled in Sprint 1")
    try:
        quote=market.ticker(symbol)
        if quote["last"] is None or market.is_stale(quote,settings.max_data_age_seconds):
            raise HTTPException(503,"Missing or stale market data; order rejected")
        return portfolio.execute(symbol,order.side.lower(),order.quantity,float(quote["last"]),
            settings.fee_rate,settings.slippage_rate,settings.max_order_fraction,settings.min_order_value_eur)
    except PortfolioError as exc: raise HTTPException(422,str(exc)) from exc
    except MarketDataError as exc: raise HTTPException(502,str(exc)) from exc


from contextlib import asynccontextmanager
from .autonomous import AutonomousAgent, AutonomousStore
from .agent_api import router as agent_router

agent_store = AutonomousStore(settings.database_path)
agent_worker = AutonomousAgent(agent_store)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent_worker.start()
    yield
    await agent_worker.stop()

app.router.lifespan_context = lifespan
app.include_router(agent_router)

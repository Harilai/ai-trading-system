import hmac
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from .autonomous import AutonomousStore
from .config import settings
from .portfolio import PaperPortfolio, PortfolioError

router = APIRouter(prefix="/api/agent", tags=["autonomous-agent"])
store = AutonomousStore(settings.database_path)
portfolio = PaperPortfolio(settings.database_path, settings.initial_cash_eur)


def require_control_token(token: str | None) -> None:
    if not settings.agent_control_token:
        raise HTTPException(503, "Agent controls disabled: configure AGENT_CONTROL_TOKEN")
    if token is None or not hmac.compare_digest(token, settings.agent_control_token):
        raise HTTPException(401, "Invalid agent control token")


class WithdrawalRequestBody(BaseModel):
    amount: float = Field(gt=0)


class TaskRequest(BaseModel):
    kind: str = Field(pattern="^(health_check|market_scan|equity_research|decision_cycle)$")
    payload: dict = Field(default_factory=dict)


@router.get("/status")
def status(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return store.overview()


@router.post("/resume")
def resume(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    state = store.state()
    if state["emergency_stop"]:
        raise HTTPException(409, "Reset emergency stop before resuming")
    return store.set_enabled(True)


@router.post("/pause")
def pause(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return store.set_enabled(False)


@router.post("/emergency-stop")
def stop(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return store.emergency_stop()


@router.post("/reset-emergency-stop")
def reset_stop(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return store.reset_emergency_stop()


@router.post("/tasks", status_code=202)
def enqueue_task(task: TaskRequest, x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return store.enqueue(task.kind, task.payload)


@router.get("/strategy-history")
def strategy_history(
    limit: int = Query(default=100, ge=1, le=1000),
    strategy_id: str | None = None,
    instrument_id: str | None = None,
    x_agent_token: str | None = Header(default=None),
):
    require_control_token(x_agent_token)
    try:
        return {
            "items": store.strategy_history(
                limit=limit,
                strategy_id=strategy_id,
                instrument_id=instrument_id,
            )
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/withdrawals")
def withdrawals(x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    return portfolio.withdrawal_history()


@router.post("/withdrawals", status_code=202)
def request_withdrawal(
    request: WithdrawalRequestBody,
    x_agent_token: str | None = Header(default=None),
):
    require_control_token(x_agent_token)
    try:
        result = portfolio.request_withdrawal(request.amount)
        store.audit("withdrawal_requested", result)
        return result
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/withdrawals/{request_id}/approve")
def approve_withdrawal(request_id: str, x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    try:
        result = portfolio.approve_withdrawal(request_id)
        store.audit("withdrawal_approved", result)
        return result
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/withdrawals/{request_id}/cancel")
def cancel_withdrawal(request_id: str, x_agent_token: str | None = Header(default=None)):
    require_control_token(x_agent_token)
    try:
        result = portfolio.cancel_withdrawal(request_id)
        store.audit("withdrawal_cancelled", result)
        return result
    except PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc

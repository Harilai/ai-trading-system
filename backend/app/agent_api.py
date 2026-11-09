import hmac
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from .autonomous import AutonomousStore
from .config import settings

router = APIRouter(prefix="/api/agent", tags=["autonomous-agent"])
store = AutonomousStore(settings.database_path)


def require_control_token(token: str | None) -> None:
    if not settings.agent_control_token:
        raise HTTPException(503, "Agent controls disabled: configure AGENT_CONTROL_TOKEN")
    if token is None or not hmac.compare_digest(token, settings.agent_control_token):
        raise HTTPException(401, "Invalid agent control token")


class TaskRequest(BaseModel):
    kind: str = Field(pattern="^(health_check|market_scan)$")
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

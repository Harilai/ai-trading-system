from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_APP_FILE = Path(__file__).resolve()
PROJECT_ROOT = (
    _APP_FILE.parents[2]
    if (_APP_FILE.parents[2] / "docker-compose.yml").exists()
    else _APP_FILE.parents[1]
)

class Settings(BaseSettings):
    database_path: str = "data/trading.db"
    initial_cash_eur: float = 20.0
    exchange_id: str = "kraken"
    market_data_provider_ids: str = "kraken"
    twelve_data_api_key: str = ""
    twelve_data_state_path: str = str(PROJECT_ROOT / "data" / "twelve_data_state.sqlite3")
    twelve_data_daily_credit_budget: int = Field(default=750, ge=1, le=800)
    twelve_data_min_request_interval_seconds: float = Field(default=7.5, ge=0)
    fee_rate: float = 0.0026
    slippage_rate: float = 0.0005
    max_order_fraction: float = 0.25
    min_order_value_eur: float = 1.0
    max_data_age_seconds: int = 60
    agent_control_token: str = ""
    decision_cycle_interval_seconds: int = Field(default=300, ge=60)
    equity_research_interval_seconds: int = Field(default=21600, ge=3600)
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

settings = Settings()

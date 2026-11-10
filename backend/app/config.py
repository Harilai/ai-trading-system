from pathlib import Path
from decimal import Decimal

from pydantic import Field, SecretStr
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
    binance_readonly_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    binance_readonly_api_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    binance_spot_demo_enabled: bool = False
    binance_spot_demo_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    binance_spot_demo_api_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    binance_spot_demo_max_order_quote: Decimal = Field(default=Decimal("25"), gt=0, le=Decimal("1000"))
    twelve_data_state_path: str = str(PROJECT_ROOT / "data" / "twelve_data_state.sqlite3")
    twelve_data_daily_credit_budget: int = Field(default=750, ge=1, le=800)
    twelve_data_min_request_interval_seconds: float = Field(default=7.5, ge=0)
    fee_rate: float = 0.0026
    slippage_rate: float = 0.0005
    max_order_fraction: float = 0.25
    max_stop_distance_fraction: float = Field(default=0.05, gt=0, le=1)
    min_order_value_eur: float = 1.0
    max_data_age_seconds: int = 60
    max_fx_data_age_seconds: int = Field(default=300, ge=1, le=3600)
    fx_conversion_fee_rate: float = Field(default=0.001, ge=0, lt=1)
    equity_paper_fee_rate: float = Field(default=0.001, ge=0, lt=1)
    equity_paper_slippage_rate: float = Field(default=0.001, ge=0, lt=1)
    max_equity_order_fraction: float = Field(default=0.10, gt=0, le=1)
    max_equity_position_fraction: float = Field(default=0.20, gt=0, le=1)
    max_equity_stop_distance_fraction: float = Field(default=0.05, gt=0, le=1)
    min_order_value_usd: float = Field(default=1.0, ge=0)
    max_equity_spread_fraction: float = Field(default=0.02, gt=0, le=1)
    agent_control_token: str = ""
    decision_cycle_interval_seconds: int = Field(default=300, ge=60)
    equity_research_interval_seconds: int = Field(default=21600, ge=3600)
    binance_demo_reconciliation_interval_seconds: int = Field(default=30, ge=15, le=3600)
    backup_interval_seconds: int = Field(default=86400, ge=3600)
    backup_retry_interval_seconds: int = Field(default=300, ge=60, le=3600)
    backup_retention_days: int = Field(default=14, ge=1, le=365)
    backup_directory: str = str(PROJECT_ROOT / "data" / "backups")
    backup_external_enabled: bool = False
    backup_s3_bucket: str = ""
    backup_s3_region: str = "us-east-1"
    backup_s3_endpoint_url: str = ""
    backup_s3_prefix: str = "ai-trading-system"
    backup_s3_access_key_id: str = ""
    backup_s3_secret_access_key: str = ""
    backup_s3_session_token: str = ""
    backup_s3_server_side_encryption: str = "AES256"
    backup_s3_kms_key_id: str = ""
    backup_s3_allow_insecure_endpoint: bool = False
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

settings = Settings()

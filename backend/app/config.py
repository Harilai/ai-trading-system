from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_path: str = "data/trading.db"
    initial_cash_eur: float = 20.0
    exchange_id: str = "kraken"
    fee_rate: float = 0.0026
    slippage_rate: float = 0.0005
    max_order_fraction: float = 0.25
    min_order_value_eur: float = 1.0
    max_data_age_seconds: int = 60
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

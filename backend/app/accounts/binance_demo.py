from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable


class BinanceDemoError(RuntimeError):
    pass


class BinanceSpotDemoAccount:
    """Binance Spot Demo Trading adapter. Refuses production REST endpoints."""

    provider_id = "ccxt:binance-demo"
    account_alias = "binance-spot-demo"

    def __init__(
        self,
        api_key: str,
        secret: str,
        *,
        enabled: bool,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
    ):
        if enabled is not True:
            raise BinanceDemoError("Binance Spot Demo Trading connector is disabled")
        if not isinstance(api_key, str) or not api_key.strip():
            raise BinanceDemoError("Binance Spot Demo API key is required")
        if not isinstance(secret, str) or not secret.strip():
            raise BinanceDemoError("Binance Spot Demo API secret is required")
        if exchange_factory is None:
            try:
                import ccxt
            except ImportError:
                raise BinanceDemoError("CCXT is required for Binance Spot Demo Trading") from None
            exchange_factory = ccxt.binance

        try:
            exchange = exchange_factory({
                "apiKey": api_key.strip(),
                "secret": secret.strip(),
                "enableRateLimit": True,
                "timeout": 10000,
                "options": {"defaultType": "spot"},
            })
            # CCXT Binance Demo Trading uses this mode; legacy set_sandbox_mode
            # routes to the retired/old Spot testnet and must not be used here.
            enable_demo = getattr(exchange, "enable_demo_trading", None)
            if not callable(enable_demo):
                raise BinanceDemoError("Installed CCXT does not support Binance Demo Trading")
            enable_demo(True)
            self._verify_demo_endpoints(exchange)
        except BinanceDemoError:
            raise
        except Exception as exc:
            raise BinanceDemoError(
                f"Unable to initialize isolated Binance Spot Demo Trading ({type(exc).__name__})"
            ) from None
        self._exchange = exchange

    @staticmethod
    def _verify_demo_endpoints(exchange: Any) -> None:
        urls = getattr(exchange, "urls", None)
        api_urls = urls.get("api") if isinstance(urls, dict) else None
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, str):
                if value.startswith(("https://", "http://")):
                    found.append(value.lower())
            elif isinstance(value, dict):
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(api_urls)
        if not found or not any("demo-api.binance.com" in url for url in found):
            raise BinanceDemoError("CCXT did not route REST requests to Binance Demo Trading")
        if any("api.binance.com" in url and "demo-api.binance.com" not in url for url in found):
            raise BinanceDemoError("Production Binance REST endpoint detected; refusing to initialize")

    def describe(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "venue_type": "cex",
            "market_types": ["spot"],
            "environment": "demo",
            "demo_mode": True,
            "demo_only": True,
            "configured": True,
            "balances": True,
            "market_discovery": True,
            "order_submission": False,
            "order_management": False,
            "withdrawals": False,
            "transfers": False,
            "production_endpoint_allowed": False,
            "credential_scope_required": "Binance Demo Trading API credentials only; never use production API keys",
        }

    @staticmethod
    def _amount(value: Any, *, asset: str, field: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise BinanceDemoError(f"Malformed {field} balance for {asset}")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise BinanceDemoError(f"Malformed {field} balance for {asset}") from None
        if not amount.is_finite() or amount < 0:
            raise BinanceDemoError(f"Invalid {field} balance for {asset}")
        if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
            raise BinanceDemoError(f"{field} balance precision is unsupported for {asset}")
        return amount

    @staticmethod
    def _amount_text(value: Decimal | None) -> str | None:
        if value is None:
            return None
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text

    def fetch_balances(self) -> dict:
        try:
            response = self._exchange.fetch_balance()
        except Exception as exc:
            raise BinanceDemoError(
                f"Binance Spot Demo balance request failed ({type(exc).__name__})"
            ) from None
        if not isinstance(response, dict):
            raise BinanceDemoError("Binance Spot Demo returned an invalid balance response")
        maps = [response.get(name, {}) for name in ("free", "used", "total")]
        maps = [{} if item is None else item for item in maps]
        if not all(isinstance(item, dict) for item in maps):
            raise BinanceDemoError("Binance Spot Demo returned malformed balance collections")
        free_map, used_map, total_map = maps

        balances = []
        assets = set(free_map) | set(used_map) | set(total_map)
        for raw_asset in sorted(assets, key=lambda item: str(item).upper()):
            asset = str(raw_asset).strip().upper()
            if not re.fullmatch(r"[A-Z0-9._-]{1,24}", asset):
                raise BinanceDemoError("Binance Spot Demo returned an invalid asset code")
            free = self._amount(free_map.get(raw_asset), asset=asset, field="free")
            used = self._amount(used_map.get(raw_asset), asset=asset, field="used")
            total = self._amount(total_map.get(raw_asset), asset=asset, field="total")
            if total is None and free is not None and used is not None:
                total = free + used
            if any(value is not None and value > 0 for value in (free, used, total)):
                balances.append({
                    "asset": asset,
                    "free": self._amount_text(free),
                    "used": self._amount_text(used),
                    "total": self._amount_text(total),
                })

        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "market_type": "spot",
            "environment": "demo",
            "read_only": True,
            "execution_enabled": False,
            "withdrawals_enabled": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "balances": balances,
            "balance_count": len(balances),
        }

    def discover_spot_markets(self, *, limit: int = 100) -> dict:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise BinanceDemoError("Demo market limit must be between 1 and 500")
        try:
            markets = self._exchange.load_markets()
        except Exception as exc:
            raise BinanceDemoError(
                f"Binance Spot Demo market discovery failed ({type(exc).__name__})"
            ) from None
        result = []
        for symbol, market in sorted(markets.items()):
            if not market.get("spot") or market.get("active") is False:
                continue
            result.append({
                "symbol": symbol,
                "base": market.get("base"),
                "quote": market.get("quote"),
                "active": market.get("active"),
                "precision": market.get("precision") or {},
                "limits": market.get("limits") or {},
            })
            if len(result) >= limit:
                break
        return {
            "provider_id": self.provider_id,
            "environment": "demo",
            "demo_only": True,
            "discovered": len(result),
            "markets": result,
        }

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Callable


class AccountConnectorError(RuntimeError):
    pass


class BinanceSpotReadOnlyAccount:
    """Read-only Binance spot account connector; intentionally exposes no order methods."""

    provider_id = "ccxt:binance"
    account_alias = "binance-spot-readonly"

    def __init__(
        self,
        api_key: str,
        secret: str,
        *,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise AccountConnectorError("Binance read-only API key is required")
        if not isinstance(secret, str) or not secret.strip():
            raise AccountConnectorError("Binance read-only API secret is required")
        if exchange_factory is None:
            try:
                import ccxt
            except ImportError as exc:
                raise AccountConnectorError("CCXT is required for Binance account access") from exc
            exchange_factory = ccxt.binance

        try:
            self._exchange = exchange_factory({
                "apiKey": api_key.strip(),
                "secret": secret.strip(),
                "enableRateLimit": True,
                "timeout": 10000,
                "options": {"defaultType": "spot"},
            })
        except Exception as exc:
            raise AccountConnectorError(
                f"Unable to initialize Binance read-only connector ({type(exc).__name__})"
            ) from None

    def describe(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "venue_type": "cex",
            "market_types": ["spot"],
            "configured": True,
            "read_only_connector": True,
            "balances": True,
            "positions": False,
            "order_management": False,
            "order_submission": False,
            "withdrawals": False,
            "transfers": False,
            "wallet_signing": False,
            "credential_scope_required": "read-only; trading and withdrawal permissions must be disabled at Binance",
            "account_key_permissions_verified": False,
        }

    @staticmethod
    def _amount(value: Any, *, asset: str, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise AccountConnectorError(f"Binance returned malformed {field} balance for {asset}")
        try:
            amount = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AccountConnectorError(
                f"Binance returned malformed {field} balance for {asset}"
            ) from exc
        if not math.isfinite(amount) or amount < 0:
            raise AccountConnectorError(f"Binance returned invalid {field} balance for {asset}")
        return amount

    def fetch_balances(self) -> dict:
        """Fetch spot balances only; no order, transfer or withdrawal API is called."""
        try:
            response = self._exchange.fetch_balance()
        except Exception as exc:
            # Do not return raw provider exceptions: they may contain request details.
            raise AccountConnectorError(
                f"Binance spot balance request failed ({type(exc).__name__})"
            ) from None

        if not isinstance(response, dict):
            raise AccountConnectorError("Binance returned an invalid balance response")
        free_map = response.get("free", {})
        used_map = response.get("used", {})
        total_map = response.get("total", {})
        free_map = {} if free_map is None else free_map
        used_map = {} if used_map is None else used_map
        total_map = {} if total_map is None else total_map
        if not all(isinstance(value, dict) for value in (free_map, used_map, total_map)):
            raise AccountConnectorError("Binance returned malformed balance collections")

        assets = set(free_map) | set(used_map) | set(total_map)
        balances = []
        for raw_asset in sorted(assets, key=lambda value: str(value).upper()):
            asset = str(raw_asset).strip().upper()
            if not re.fullmatch(r"[A-Z0-9._-]{1,24}", asset):
                raise AccountConnectorError("Binance returned an invalid asset code")
            free = self._amount(free_map.get(raw_asset), asset=asset, field="free")
            used = self._amount(used_map.get(raw_asset), asset=asset, field="used")
            total = self._amount(total_map.get(raw_asset), asset=asset, field="total")
            if total is None and free is not None and used is not None:
                total = free + used
            if total is None and free is None and used is None:
                continue
            if any(value is not None and value > 0 for value in (free, used, total)):
                balances.append({
                    "asset": asset,
                    "free": free,
                    "used": used,
                    "total": total,
                })

        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "market_type": "spot",
            "read_only": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "balances": balances,
            "balance_count": len(balances),
            "execution_enabled": False,
            "withdrawals_enabled": False,
        }

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from threading import RLock
from typing import Any


class CcxtReadOnlyAccountError(RuntimeError):
    pass


class CcxtReadOnlyAccount:
    """Authenticated CCXT account adapter restricted to Spot balance reads."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        *,
        account_alias: str | None = None,
        exchange_factory=None,
    ):
        normalized_id = exchange_id.strip().lower() if isinstance(exchange_id, str) else ""
        if not re.fullmatch(r"[a-z0-9_]{2,32}", normalized_id):
            raise CcxtReadOnlyAccountError("CCXT exchange ID is invalid")
        if not isinstance(api_key, str) or not api_key.strip():
            raise CcxtReadOnlyAccountError("CCXT read-only API key is required")
        if not isinstance(api_secret, str) or not api_secret.strip():
            raise CcxtReadOnlyAccountError("CCXT read-only API secret is required")
        alias = account_alias or f"{normalized_id}-spot-readonly"
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", alias.strip()):
            raise CcxtReadOnlyAccountError("CCXT account alias is invalid")

        options = {
            "apiKey": api_key.strip(),
            "secret": api_secret.strip(),
            "enableRateLimit": True,
            "timeout": 10000,
            "options": {"defaultType": "spot"},
        }
        if exchange_factory is None:
            try:
                import ccxt
            except ImportError as exc:
                raise CcxtReadOnlyAccountError("CCXT is required for private account reads") from exc
            exchange_class = getattr(ccxt, normalized_id, None)
            if exchange_class is None:
                raise CcxtReadOnlyAccountError(f"Unsupported CCXT exchange: {normalized_id}")
            try:
                exchange = exchange_class(options)
            except Exception as exc:
                raise CcxtReadOnlyAccountError(
                    f"Unable to initialize CCXT account adapter ({type(exc).__name__})"
                ) from None
        else:
            try:
                exchange = exchange_factory(options)
            except Exception as exc:
                raise CcxtReadOnlyAccountError(
                    f"Unable to initialize CCXT account adapter ({type(exc).__name__})"
                ) from None

        capabilities = getattr(exchange, "has", {})
        if isinstance(capabilities, dict) and capabilities.get("fetchBalance") is False:
            raise CcxtReadOnlyAccountError("Selected CCXT exchange does not support balance reads")
        self.exchange_id = normalized_id
        self.provider_id = f"ccxt:{normalized_id}"
        self.account_alias = alias.strip()
        self._exchange = exchange
        self._lock = RLock()

    @staticmethod
    def _amount(value: Any, *, asset: str, field: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise CcxtReadOnlyAccountError(f"Exchange returned malformed {field} balance for {asset}")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise CcxtReadOnlyAccountError(f"Exchange returned malformed {field} balance for {asset}") from None
        if not amount.is_finite() or amount < 0:
            raise CcxtReadOnlyAccountError(f"Exchange returned invalid {field} balance for {asset}")
        if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
            raise CcxtReadOnlyAccountError(f"Exchange returned unsupported {field} precision for {asset}")
        return amount

    @staticmethod
    def _amount_text(value: Decimal | None) -> str | None:
        if value is None:
            return None
        normalized = format(value, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized or "0"

    def describe(self) -> dict:
        rate_limit = getattr(self._exchange, "rateLimit", None)
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "venue_type": "cex",
            "market_types": ["spot"],
            "environment": "production",
            "configured": True,
            "read_only_connector": True,
            "balances": True,
            "positions": False,
            "order_management": False,
            "order_submission": False,
            "order_cancellation": False,
            "reconciliation": False,
            "transfers": False,
            "withdrawals": False,
            "wallet_signing": False,
            "account_key_permissions_verified": False,
            "rate_limit_ms": int(rate_limit) if isinstance(rate_limit, (int, float)) and rate_limit >= 0 else None,
            "credential_scope_required": "read-only Spot balance permissions only",
            "restrictions": [
                "CCXT authenticated Spot balance reads only",
                "No order submission, cancellation, transfer, withdrawal or wallet signing methods",
                "The exchange API key's actual permissions are not independently verified",
            ],
        }

    def fetch_balances(self) -> dict:
        """Read Spot balances only. This adapter exposes no exchange write operation."""
        try:
            with self._lock:
                response = self._exchange.fetch_balance()
        except Exception as exc:
            raise CcxtReadOnlyAccountError(
                f"CCXT account balance request failed ({type(exc).__name__})"
            ) from None
        if not isinstance(response, dict):
            raise CcxtReadOnlyAccountError("Exchange returned an invalid balance response")
        free_map = response.get("free") or {}
        used_map = response.get("used") or {}
        total_map = response.get("total") or {}
        if not all(isinstance(item, dict) for item in (free_map, used_map, total_map)):
            raise CcxtReadOnlyAccountError("Exchange returned malformed balance collections")

        assets = set(free_map) | set(used_map) | set(total_map)
        balances = []
        for raw_asset in sorted(assets, key=lambda value: str(value).upper()):
            asset = str(raw_asset).strip().upper()
            if not re.fullmatch(r"[A-Z0-9._-]{1,24}", asset):
                raise CcxtReadOnlyAccountError("Exchange returned an invalid asset code")
            free = self._amount(free_map.get(raw_asset), asset=asset, field="free")
            used = self._amount(used_map.get(raw_asset), asset=asset, field="used")
            total = self._amount(total_map.get(raw_asset), asset=asset, field="total")
            if total is None and free is not None and used is not None:
                total = free + used
            if free is None and used is None and total is None:
                continue
            if not any(value is not None and value > 0 for value in (free, used, total)):
                continue
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
            "environment": "production",
            "read_only": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "balances": balances,
            "balance_count": len(balances),
            "execution_enabled": False,
            "withdrawals_enabled": False,
            "transfers_enabled": False,
        }

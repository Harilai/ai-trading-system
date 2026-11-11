from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

import httpx


PAPER_API_BASE_URL = "https://paper-api.alpaca.markets"
_DECIMAL = re.compile(r"-?\\d+(?:\\.\\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
_SYMBOL = re.compile(r"[A-Za-z0-9._-]{1,32}")


class AlpacaPaperAccountError(RuntimeError):
    """Safe, non-sensitive error raised by the Alpaca Paper read-only adapter."""


class AlpacaPaperReadOnlyAccount:
    """Read-only Alpaca Paper account/positions adapter; exposes GET operations only."""

    provider_id = "alpaca:paper"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        account_alias: str = "alpaca-paper-readonly",
        expected_account_id: str | None = None,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise AlpacaPaperAccountError("Alpaca Paper API key is required")
        if not isinstance(api_secret, str) or not api_secret.strip():
            raise AlpacaPaperAccountError("Alpaca Paper API secret is required")
        if not isinstance(account_alias, str) or not _IDENTIFIER.fullmatch(account_alias.strip()):
            raise AlpacaPaperAccountError("Alpaca Paper account alias is invalid")
        if expected_account_id is not None and (
            not isinstance(expected_account_id, str) or not _IDENTIFIER.fullmatch(expected_account_id.strip())
        ):
            raise AlpacaPaperAccountError("Expected Alpaca account ID is invalid")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
            raise AlpacaPaperAccountError("Alpaca request timeout is invalid")

        self.account_alias = account_alias.strip()
        self.expected_account_id = expected_account_id.strip() if expected_account_id else None
        self._client = client or httpx.Client(
            base_url=PAPER_API_BASE_URL,
            headers={
                "APCA-API-KEY-ID": api_key.strip(),
                "APCA-API-SECRET-KEY": api_secret.strip(),
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def describe(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "venue_type": "broker",
            "environment": "paper",
            "market_types": ["cash"],
            "configured": True,
            "read_only_connector": True,
            "balances": False,
            "positions": True,
            "order_management": False,
            "order_submission": False,
            "order_cancellation": False,
            "reconciliation": False,
            "transfers": False,
            "withdrawals": False,
            "wallet_signing": False,
            "account_key_permissions_verified": False,
            "restrictions": [
                "Fixed Alpaca Paper API host; no production endpoint or host override",
                "Authenticated GET /v2/account identity check and GET /v2/positions only",
                "No order, cancellation, liquidation, transfer, withdrawal or wallet-signing operations",
                "Positions are external observations and are not imported into the paper portfolio",
                "ETF subtype is not inferred from the positions endpoint; us_equity is normalized as equity",
            ],
        }

    def _get_json(self, path: str) -> Any:
        try:
            response = self._client.get(path)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise AlpacaPaperAccountError("Alpaca Paper request timed out") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise AlpacaPaperAccountError(f"Alpaca Paper request failed with HTTP {status}") from None
        except httpx.HTTPError as exc:
            raise AlpacaPaperAccountError(
                f"Alpaca Paper request failed ({type(exc).__name__})"
            ) from None
        except ValueError:
            raise AlpacaPaperAccountError("Alpaca Paper returned malformed JSON") from None

    @staticmethod
    def _decimal(value: Any, field: str, *, signed: bool = False, positive: bool = False) -> str:
        if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
            raise AlpacaPaperAccountError(f"Alpaca position {field} is not a decimal string")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise AlpacaPaperAccountError(f"Alpaca position {field} is invalid") from None
        if not amount.is_finite() or (not signed and amount < 0) or (positive and amount <= 0):
            raise AlpacaPaperAccountError(f"Alpaca position {field} is outside the accepted range")
        if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
            raise AlpacaPaperAccountError(f"Alpaca position {field} precision is unsupported")
        normalized = format(amount, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized or "0"

    def _verified_account(self) -> str:
        account = self._get_json("/v2/account")
        if not isinstance(account, dict):
            raise AlpacaPaperAccountError("Alpaca Paper account response is invalid")
        account_id = account.get("id")
        if not isinstance(account_id, str) or not _IDENTIFIER.fullmatch(account_id):
            raise AlpacaPaperAccountError("Alpaca Paper account identity is missing or invalid")
        if self.expected_account_id and account_id != self.expected_account_id:
            raise AlpacaPaperAccountError("Alpaca Paper account identity does not match configuration")
        return account_id

    def fetch_positions_snapshot(self, *, market_type: str) -> dict:
        if market_type != "cash":
            raise AlpacaPaperAccountError("Alpaca Paper positions support cash market_type only")

        # A successful, authenticated account identity read is required before an
        # empty positions list can be interpreted as a complete zero-position state.
        self._verified_account()
        raw_positions = self._get_json("/v2/positions")
        if not isinstance(raw_positions, list):
            raise AlpacaPaperAccountError("Alpaca Paper positions response must be a list")

        positions = []
        seen_asset_ids = set()
        for raw in raw_positions:
            if not isinstance(raw, dict):
                raise AlpacaPaperAccountError("Alpaca Paper position record is invalid")
            asset_id = raw.get("asset_id")
            symbol = raw.get("symbol")
            asset_class = raw.get("asset_class")
            if not isinstance(asset_id, str) or not _IDENTIFIER.fullmatch(asset_id):
                raise AlpacaPaperAccountError("Alpaca position asset_id is missing or invalid")
            if asset_id in seen_asset_ids:
                raise AlpacaPaperAccountError("Alpaca returned duplicate asset identities")
            seen_asset_ids.add(asset_id)
            if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
                raise AlpacaPaperAccountError("Alpaca position symbol is missing or invalid")
            if asset_class != "us_equity":
                raise AlpacaPaperAccountError("Alpaca returned an unsupported asset class")

            quantity = self._decimal(raw.get("qty"), "quantity", positive=True)
            side = raw.get("side")
            if side not in {"long", "short"}:
                raise AlpacaPaperAccountError("Alpaca position side is invalid")

            positions.append({
                "instrument_id": asset_id,
                "symbol": symbol.upper(),
                "asset_class": "equity",
                "market_type": "cash",
                "quantity": quantity,
                "side": side,
                "quote_currency": "USD",
                "average_entry_price": self._decimal(raw["avg_entry_price"], "average entry price", positive=True)
                    if raw.get("avg_entry_price") is not None else None,
                "mark_price": self._decimal(raw["current_price"], "current price", positive=True)
                    if raw.get("current_price") is not None else None,
                "market_value": self._decimal(raw["market_value"], "market value", signed=True)
                    if raw.get("market_value") is not None else None,
                "unrealized_pnl": self._decimal(raw["unrealized_pl"], "unrealized P/L", signed=True)
                    if raw.get("unrealized_pl") is not None else None,
            })

        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "environment": "paper",
            "market_type": "cash",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "execution_enabled": False,
            "withdrawals_enabled": False,
            "coverage_status": "complete",
            "positions": sorted(positions, key=lambda item: item["instrument_id"]),
        }

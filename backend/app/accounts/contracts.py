from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import re
from threading import RLock
import time
from typing import Any, Protocol, runtime_checkable


class AccountProviderError(RuntimeError):
    pass


class AccountProviderNotFound(AccountProviderError):
    pass


class AmbiguousAccountProvider(AccountProviderError):
    pass


class UnsupportedAccountCapability(AccountProviderError):
    pass


class InvalidAccountRequest(AccountProviderError):
    pass


def _decimal_text(value: Any, *, field: str, allow_zero: bool = True) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise AccountProviderError(f"Account {field} must be a plain decimal string")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise AccountProviderError(f"Account {field} is malformed") from None
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise AccountProviderError(f"Account {field} must be finite and non-negative")
    if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
        raise AccountProviderError(f"Account {field} precision is unsupported")
    normalized = format(amount, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


@dataclass(frozen=True)
class AccountCapabilities:
    provider_id: str
    account_alias: str
    venue_type: str
    environment: str
    market_types: tuple[str, ...]
    balances: bool
    positions: bool
    order_management: bool
    order_submission: bool
    order_cancellation: bool
    reconciliation: bool
    read_only: bool
    transfers: bool
    withdrawals: bool
    wallet_signing: bool
    permissions_verified: bool
    restrictions: tuple[str, ...]

    @classmethod
    def from_description(cls, description: dict) -> "AccountCapabilities":
        if not isinstance(description, dict):
            raise AccountProviderError("Account provider description must be an object")
        if description.get("configured") is not True:
            raise AccountProviderError("Only configured account providers can be registered")
        provider_id = description.get("provider_id")
        account_alias = description.get("account_alias")
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 128:
            raise AccountProviderError("Account provider_id is invalid")
        if not isinstance(account_alias, str) or not account_alias.strip() or len(account_alias) > 128:
            raise AccountProviderError("Account account_alias is invalid")
        market_types = description.get("market_types", [])
        if not isinstance(market_types, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in market_types
        ):
            raise AccountProviderError("Account market_types are invalid")
        flags = {
            "balances": description.get("balances", False),
            "positions": description.get("positions", False),
            "order_management": description.get("order_management", False),
            "order_submission": description.get("order_submission", False),
            "order_cancellation": description.get("order_cancellation", False),
            "reconciliation": description.get("reconciliation", False),
            "read_only": description.get("read_only_connector", False),
            "transfers": description.get("transfers", False),
            "withdrawals": description.get("withdrawals", False),
            "wallet_signing": description.get("wallet_signing", False),
            "permissions_verified": description.get("account_key_permissions_verified", False),
        }
        if any(not isinstance(value, bool) for value in flags.values()):
            raise AccountProviderError("Account capability flags must be explicit booleans")
        if flags["read_only"] and (
            flags["order_management"] or flags["order_submission"] or flags["order_cancellation"]
            or flags["transfers"] or flags["withdrawals"] or flags["wallet_signing"]
        ):
            raise AccountProviderError("Read-only account capability conflicts with write permissions")
        restrictions = description.get("restrictions", [])
        if not isinstance(restrictions, (list, tuple)) or any(not isinstance(item, str) for item in restrictions):
            raise AccountProviderError("Account restrictions must be text values")
        venue_type = description.get("venue_type", "unknown")
        environment = description.get("environment", "unspecified")
        if not isinstance(venue_type, str) or not venue_type.strip():
            raise AccountProviderError("Account venue_type is invalid")
        if not isinstance(environment, str) or not environment.strip():
            raise AccountProviderError("Account environment is invalid")
        return cls(
            provider_id=provider_id.strip(),
            account_alias=account_alias.strip(),
            venue_type=venue_type.strip().lower(),
            environment=environment.strip().lower(),
            market_types=tuple(sorted(set(market_types))),
            restrictions=tuple(str(item) for item in restrictions),
            **flags,
        )

    def to_dict(self) -> dict:
        result = asdict(self)
        result["market_types"] = list(self.market_types)
        result["restrictions"] = list(self.restrictions)
        return result


@dataclass(frozen=True)
class AccountBalance:
    asset: str
    free: str | None
    used: str | None
    total: str | None

    @classmethod
    def normalize(cls, raw: dict) -> "AccountBalance":
        if not isinstance(raw, dict):
            raise AccountProviderError("Account balance record must be an object")
        asset = raw.get("asset")
        if not isinstance(asset, str) or not re.fullmatch(r"[A-Z0-9._-]{1,24}", asset.strip().upper()):
            raise AccountProviderError("Account balance asset code is invalid")
        free = _decimal_text(raw.get("free"), field=f"{asset} free")
        used = _decimal_text(raw.get("used"), field=f"{asset} used")
        total = _decimal_text(raw.get("total"), field=f"{asset} total")
        if free is None and used is None and total is None:
            raise AccountProviderError(f"Account balance for {asset} has no amount fields")
        if total is None and free is not None and used is not None:
            with localcontext() as context:
                context.prec = 160
                total = format(Decimal(free) + Decimal(used), "f")
        return cls(asset=asset.strip().upper(), free=free, used=used, total=total)


@dataclass(frozen=True)
class AccountBalanceSnapshot:
    provider_id: str
    account_alias: str
    market_type: str
    environment: str
    fetched_at: str
    read_only: bool
    execution_enabled: bool
    withdrawals_enabled: bool
    transfers_enabled: bool
    wallet_signing_enabled: bool
    permissions_verified: bool
    balances: tuple[AccountBalance, ...]

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "market_type": self.market_type,
            "environment": self.environment,
            "fetched_at": self.fetched_at,
            "read_only": self.read_only,
            "execution_enabled": self.execution_enabled,
            "withdrawals_enabled": self.withdrawals_enabled,
            "transfers_enabled": self.transfers_enabled,
            "wallet_signing_enabled": self.wallet_signing_enabled,
            "permissions_verified": self.permissions_verified,
            "balances": [asdict(item) for item in self.balances],
            "balance_count": len(self.balances),
        }


@dataclass(frozen=True)
class AccountPosition:
    instrument_id: str
    symbol: str
    asset_class: str
    market_type: str
    quantity: str
    side: str
    quote_currency: str
    average_entry_price: str | None = None
    mark_price: str | None = None
    market_value: str | None = None
    unrealized_pnl: str | None = None

    @classmethod
    def normalize(cls, raw: Any) -> "AccountPosition":
        if isinstance(raw, AccountPosition):
            raw = asdict(raw)
        if not isinstance(raw, dict):
            raise AccountProviderError("Account position record must be an object")
        fields = ("instrument_id", "symbol", "asset_class", "market_type", "side", "quote_currency")
        values = {}
        for field in fields:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise AccountProviderError(f"Account position {field} is invalid")
            values[field] = value.strip()
        instrument_id = values["instrument_id"]
        symbol = values["symbol"]
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", instrument_id):
            raise AccountProviderError("Account position instrument_id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,64}", symbol):
            raise AccountProviderError("Account position symbol is invalid")
        asset_class = values["asset_class"].lower()
        market_type = values["market_type"].lower()
        side = values["side"].lower()
        quote_currency = values["quote_currency"].upper()
        if asset_class not in {"crypto", "equity", "etf", "forex", "index", "commodity", "future", "option", "bond", "other"}:
            raise AccountProviderError("Account position asset_class is unsupported")
        if market_type not in {"spot", "margin", "future", "option", "perpetual", "cash"}:
            raise AccountProviderError("Account position market_type is unsupported")
        if side not in {"long", "short"}:
            raise AccountProviderError("Account position side is invalid")
        if not re.fullmatch(r"[A-Z0-9._-]{2,16}", quote_currency):
            raise AccountProviderError("Account position quote_currency is invalid")
        quantity = _decimal_text(raw.get("quantity"), field="position quantity", allow_zero=False)
        if quantity is None:
            raise AccountProviderError("Account position quantity is required")
        optional = {}
        for field in ("average_entry_price", "mark_price", "market_value"):
            optional[field] = _decimal_text(raw.get(field), field=f"position {field}")
        pnl = raw.get("unrealized_pnl")
        if pnl is not None:
            if not isinstance(pnl, str) or not re.fullmatch(r"-?\d+(?:\.\d+)?", pnl):
                raise AccountProviderError("Account position unrealized_pnl must be a plain decimal string")
            amount = Decimal(pnl)
            if not amount.is_finite() or len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
                raise AccountProviderError("Account position unrealized_pnl is invalid")
            pnl = format(amount, "f")
            if "." in pnl:
                pnl = pnl.rstrip("0").rstrip(".")
        return cls(
            instrument_id=instrument_id,
            symbol=symbol,
            asset_class=asset_class,
            market_type=market_type,
            quantity=quantity,
            side=side,
            quote_currency=quote_currency,
            **optional,
            unrealized_pnl=pnl,
        )


@dataclass(frozen=True)
class AccountPositionSnapshot:
    provider_id: str
    account_alias: str
    market_type: str
    environment: str
    fetched_at: str
    positions: tuple[AccountPosition, ...]
    coverage_status: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "market_type": self.market_type,
            "environment": self.environment,
            "fetched_at": self.fetched_at,
            "retrieved_at": self.fetched_at,
            "timestamp_source": "retrieved_at",
            "coverage_status": self.coverage_status,
            "positions": [asdict(item) for item in self.positions],
            "position_count": len(self.positions),
        }


_ACCOUNT_ORDER_STATUSES = {
    "intent_created", "submission_unknown", "pending", "open", "partially_filled",
    "filled", "cancelled", "expired", "rejected", "unknown", "reconciliation_required",
}
_ACCOUNT_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "other"}


def _required_identifier(raw: Any, *, field: str, max_length: int = 256) -> str:
    if (not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > max_length
            or any(ord(char) < 32 for char in raw)):
        raise AccountProviderError(f"Account {field} is invalid")
    return raw.strip()


def _observed_timestamp(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AccountProviderError(f"Account {field} must be a timezone-aware timestamp")
    try:
        stamp = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise AccountProviderError(f"Account {field} must be a timezone-aware timestamp") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise AccountProviderError(f"Account {field} must be a timezone-aware timestamp")
    normalized = stamp.astimezone(timezone.utc)
    if normalized.timestamp() > time.time() + 300:
        raise AccountProviderError(f"Account {field} timestamp is in the future")
    return normalized.isoformat()


@dataclass(frozen=True)
class AccountOrder:
    """Normalized order observation scoped to one provider account and environment."""

    provider_id: str
    account_alias: str
    environment: str
    market_type: str
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    requested_quantity: str
    filled_quantity: str
    quote_currency: str
    observed_at: str
    order_id: str | None = None
    client_order_id: str | None = None
    remaining_quantity: str | None = None
    average_fill_price: str | None = None
    exchange_status: str | None = None
    exchange_timestamp_ms: int | None = None

    @classmethod
    def normalize(cls, raw: Any) -> "AccountOrder":
        if isinstance(raw, AccountOrder):
            raw = asdict(raw)
        if not isinstance(raw, dict):
            raise AccountProviderError("Account order record must be an object")
        identity = {
            field: _required_identifier(raw.get(field), field=f"order {field}")
            for field in ("provider_id", "account_alias", "environment", "market_type", "instrument_id", "symbol")
        }
        identity["environment"] = identity["environment"].lower()
        identity["market_type"] = identity["market_type"].lower()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", identity["instrument_id"]):
            raise AccountProviderError("Account order instrument_id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,64}", identity["symbol"]):
            raise AccountProviderError("Account order symbol is invalid")
        side = _required_identifier(raw.get("side"), field="order side", max_length=16).lower()
        order_type = _required_identifier(raw.get("order_type"), field="order type", max_length=32).lower().replace("-", "_")
        status = _required_identifier(raw.get("status"), field="order status", max_length=40).lower().replace("-", "_")
        if status == "canceled":
            status = "cancelled"
        if side not in {"buy", "sell"}:
            raise AccountProviderError("Account order side is invalid")
        if order_type not in _ACCOUNT_ORDER_TYPES:
            raise AccountProviderError("Account order type is unsupported")
        if status not in _ACCOUNT_ORDER_STATUSES:
            raise AccountProviderError("Account order status is unsupported")
        requested = _decimal_text(raw.get("requested_quantity"), field="order requested quantity", allow_zero=False)
        filled = _decimal_text(raw.get("filled_quantity", "0"), field="order filled quantity")
        remaining = _decimal_text(raw.get("remaining_quantity"), field="order remaining quantity")
        if requested is None or filled is None:
            raise AccountProviderError("Account order quantities are required")
        requested_value, filled_value = Decimal(requested), Decimal(filled)
        if filled_value > requested_value:
            raise AccountProviderError("Account order filled quantity exceeds requested quantity")
        if remaining is not None and Decimal(remaining) + filled_value != requested_value:
            raise AccountProviderError("Account order remaining quantity conflicts with requested and filled quantities")
        if status == "filled" and (filled_value != requested_value or (remaining is not None and Decimal(remaining) != 0)):
            raise AccountProviderError("Filled account order quantities are inconsistent")
        if status == "partially_filled" and not 0 < filled_value < requested_value:
            raise AccountProviderError("Partially filled account order quantities are inconsistent")
        if status == "open" and filled_value != 0:
            raise AccountProviderError("Open account order cannot contain unreported fills")
        quote = _required_identifier(raw.get("quote_currency"), field="order quote currency", max_length=16).upper()
        if not re.fullmatch(r"[A-Z0-9._-]{2,16}", quote):
            raise AccountProviderError("Account order quote currency is invalid")
        order_id = raw.get("order_id")
        client_order_id = raw.get("client_order_id")
        if order_id is not None:
            order_id = _required_identifier(order_id, field="exchange order ID")
        if client_order_id is not None:
            client_order_id = _required_identifier(client_order_id, field="client order ID")
        if order_id is None and client_order_id is None:
            raise AccountProviderError("Account order requires an exchange ID or client order ID")
        average = _decimal_text(raw.get("average_fill_price"), field="order average fill price", allow_zero=False)
        if average is not None and filled_value == 0:
            raise AccountProviderError("Account order cannot have an average fill price without fills")
        exchange_status = raw.get("exchange_status")
        if exchange_status is not None:
            exchange_status = _required_identifier(exchange_status, field="exchange status", max_length=128)
        exchange_timestamp_ms = raw.get("exchange_timestamp_ms")
        if exchange_timestamp_ms is not None:
            if isinstance(exchange_timestamp_ms, bool) or not isinstance(exchange_timestamp_ms, int) or exchange_timestamp_ms <= 0:
                raise AccountProviderError("Account order exchange timestamp is invalid")
            if exchange_timestamp_ms > int(time.time() * 1000) + 300_000:
                raise AccountProviderError("Account order exchange timestamp is in the future")
        return cls(
            **identity, side=side, order_type=order_type, status=status,
            requested_quantity=requested, filled_quantity=filled, quote_currency=quote,
            observed_at=_observed_timestamp(raw.get("observed_at"), field="order observed_at"),
            order_id=order_id, client_order_id=client_order_id,
            remaining_quantity=remaining, average_fill_price=average,
            exchange_status=exchange_status, exchange_timestamp_ms=exchange_timestamp_ms,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AccountOrderFill:
    """One immutable execution with its own trade ID and fee provenance."""

    provider_id: str
    account_alias: str
    environment: str
    market_type: str
    instrument_id: str
    symbol: str
    side: str
    trade_id: str
    amount: str
    price: str
    quote_currency: str
    timestamp_ms: int
    order_id: str | None = None
    client_order_id: str | None = None
    cost: str | None = None
    fee_amount: str | None = None
    fee_currency: str | None = None

    @classmethod
    def normalize(cls, raw: Any) -> "AccountOrderFill":
        if isinstance(raw, AccountOrderFill):
            raw = asdict(raw)
        if not isinstance(raw, dict):
            raise AccountProviderError("Account order fill must be an object")
        identity = {
            field: _required_identifier(raw.get(field), field=f"fill {field}")
            for field in ("provider_id", "account_alias", "environment", "market_type", "instrument_id", "symbol", "trade_id")
        }
        identity["environment"] = identity["environment"].lower()
        identity["market_type"] = identity["market_type"].lower()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", identity["instrument_id"]):
            raise AccountProviderError("Account fill instrument_id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,64}", identity["symbol"]):
            raise AccountProviderError("Account fill symbol is invalid")
        side = _required_identifier(raw.get("side"), field="fill side", max_length=16).lower()
        if side not in {"buy", "sell"}:
            raise AccountProviderError("Account fill side is invalid")
        amount = _decimal_text(raw.get("amount"), field="fill amount", allow_zero=False)
        price = _decimal_text(raw.get("price"), field="fill price", allow_zero=False)
        cost = _decimal_text(raw.get("cost"), field="fill cost")
        fee_amount = _decimal_text(raw.get("fee_amount"), field="fill fee amount")
        if amount is None or price is None:
            raise AccountProviderError("Account fill amount and price are required")
        quote = _required_identifier(raw.get("quote_currency"), field="fill quote currency", max_length=16).upper()
        if not re.fullmatch(r"[A-Z0-9._-]{2,16}", quote):
            raise AccountProviderError("Account fill quote currency is invalid")
        order_id = raw.get("order_id")
        client_order_id = raw.get("client_order_id")
        if order_id is not None:
            order_id = _required_identifier(order_id, field="fill exchange order ID")
        if client_order_id is not None:
            client_order_id = _required_identifier(client_order_id, field="fill client order ID")
        if order_id is None and client_order_id is None:
            raise AccountProviderError("Account fill requires an exchange order ID or client order ID")
        fee_currency = raw.get("fee_currency")
        if fee_currency is not None:
            fee_currency = _required_identifier(fee_currency, field="fill fee currency", max_length=16).upper()
            if not re.fullmatch(r"[A-Z0-9._-]{2,16}", fee_currency):
                raise AccountProviderError("Account fill fee currency is invalid")
        if fee_amount is not None and Decimal(fee_amount) > 0 and fee_currency is None:
            raise AccountProviderError("Positive account fill fee requires an explicit fee currency")
        timestamp_ms = raw.get("timestamp_ms")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
            raise AccountProviderError("Account fill timestamp_ms is invalid")
        if timestamp_ms > int(time.time() * 1000) + 300_000:
            raise AccountProviderError("Account fill timestamp is in the future")
        return cls(
            **identity, side=side, amount=amount, price=price, cost=cost,
            quote_currency=quote, timestamp_ms=timestamp_ms, order_id=order_id,
            client_order_id=client_order_id, fee_amount=fee_amount, fee_currency=fee_currency,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class AccountBalanceProvider(Protocol):
    provider_id: str

    def describe(self) -> dict: ...
    def fetch_balances(self) -> dict: ...


@runtime_checkable
class PositionProvider(Protocol):
    def fetch_positions(self) -> list[AccountPosition]: ...


@dataclass(frozen=True)
class AccountFillHistory:
    """Provider fill response with explicit scope, observation time and completeness."""

    provider_id: str
    account_alias: str
    environment: str
    market_type: str
    instrument_id: str
    observed_at: str
    history_complete: bool
    fills: tuple[AccountOrderFill, ...]
    order_id: str | None = None
    client_order_id: str | None = None

    @classmethod
    def normalize(cls, raw: Any) -> "AccountFillHistory":
        if isinstance(raw, AccountFillHistory):
            raw = raw.to_dict()
        if not isinstance(raw, dict):
            raise AccountProviderError("Account fill history must be an object")
        identity = {
            field: _required_identifier(raw.get(field), field=f"fill history {field}")
            for field in ("provider_id", "account_alias", "environment", "market_type", "instrument_id")
        }
        identity["environment"] = identity["environment"].lower()
        identity["market_type"] = identity["market_type"].lower()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", identity["instrument_id"]):
            raise AccountProviderError("Account fill history instrument_id is invalid")
        order_id = raw.get("order_id")
        client_order_id = raw.get("client_order_id")
        if order_id is not None:
            order_id = _required_identifier(order_id, field="fill history exchange order ID")
        if client_order_id is not None:
            client_order_id = _required_identifier(client_order_id, field="fill history client order ID")
        if order_id is None and client_order_id is None:
            raise AccountProviderError("Account fill history requires an order ID")
        observed_at = _observed_timestamp(raw.get("observed_at"), field="fill history observed_at")
        history_complete = raw.get("history_complete")
        if not isinstance(history_complete, bool):
            raise AccountProviderError("Account fill history completeness must be an explicit boolean")
        raw_fills = raw.get("fills")
        if not isinstance(raw_fills, list):
            raise AccountProviderError("Account fill history fills must be a list")
        snapshot_ms = int(datetime.fromisoformat(observed_at).timestamp() * 1000)
        fills = []
        seen_trade_ids = set()
        for raw_fill in raw_fills:
            fill = AccountOrderFill.normalize(raw_fill)
            if (fill.provider_id, fill.account_alias, fill.environment, fill.market_type, fill.instrument_id) != (
                identity["provider_id"], identity["account_alias"], identity["environment"],
                identity["market_type"], identity["instrument_id"],
            ):
                raise AccountProviderError("Account fill history scope conflicts with a fill")
            if order_id is not None and fill.order_id is not None and fill.order_id != order_id:
                raise AccountProviderError("Account fill history order ID conflicts with a fill")
            if client_order_id is not None and fill.client_order_id is not None and fill.client_order_id != client_order_id:
                raise AccountProviderError("Account fill history client order ID conflicts with a fill")
            if fill.timestamp_ms > snapshot_ms + 300_000:
                raise AccountProviderError("Account fill timestamp is later than the history observation")
            if fill.trade_id in seen_trade_ids:
                raise AccountProviderError("Account fill history contains duplicate trade IDs")
            seen_trade_ids.add(fill.trade_id)
            fills.append(fill)
        return cls(
            **identity,
            observed_at=observed_at,
            history_complete=history_complete,
            fills=tuple(fills),
            order_id=order_id,
            client_order_id=client_order_id,
        )

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "environment": self.environment,
            "market_type": self.market_type,
            "instrument_id": self.instrument_id,
            "observed_at": self.observed_at,
            "history_complete": self.history_complete,
            "fills": [fill.to_dict() for fill in self.fills],
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
        }

@runtime_checkable
class OrderReconciliationProvider(Protocol):
    def list_open_orders(self) -> list[AccountOrder]: ...
    def fetch_order(self, order_id: str, instrument_id: str) -> AccountOrder | None: ...
    def fetch_fills(self, order_id: str, instrument_id: str) -> AccountFillHistory: ...


class AccountProviderRegistry:
    """Registry and strict normalizer for separately configured venue accounts."""

    def __init__(self):
        self._providers: dict[tuple[str, str], AccountBalanceProvider] = {}
        self._capabilities: dict[tuple[str, str], AccountCapabilities] = {}
        self._locks: dict[tuple[str, str], RLock] = {}
        self._lock = RLock()

    def register(self, provider: Any) -> AccountCapabilities:
        if not callable(getattr(provider, "describe", None)):
            raise AccountProviderError("Account provider must expose describe")
        capabilities = AccountCapabilities.from_description(provider.describe())
        has_balance_reader = callable(getattr(provider, "fetch_balances", None))
        has_position_reader = (
            callable(getattr(provider, "fetch_positions", None))
            or callable(getattr(provider, "fetch_positions_snapshot", None))
        )
        if capabilities.balances and not has_balance_reader:
            raise AccountProviderError("Provider declares balances but has no balance reader")
        if capabilities.positions and not has_position_reader:
            raise AccountProviderError("Provider declares positions but has no position reader")
        if not capabilities.balances and not capabilities.positions:
            raise UnsupportedAccountCapability("Provider must declare balance or position support")
        key = (capabilities.provider_id, capabilities.account_alias)
        with self._lock:
            if key in self._providers:
                raise AccountProviderError("Account provider is already registered")
            self._providers[key] = provider
            self._capabilities[key] = capabilities
            self._locks[key] = RLock()
        return capabilities

    def _resolve(self, provider_id: str, account_alias: str | None = None):
        with self._lock:
            matches = [
                key for key in self._providers
                if key[0] == provider_id and (account_alias is None or key[1] == account_alias)
            ]
            if not matches:
                raise AccountProviderNotFound("Account provider is not configured")
            if len(matches) > 1:
                raise AmbiguousAccountProvider("Account provider ID is ambiguous; specify account_alias")
            key = matches[0]
            return self._providers[key], self._capabilities[key], self._locks[key]

    def list_accounts(self) -> list[dict]:
        with self._lock:
            return [self._capabilities[key].to_dict() for key in sorted(self._capabilities)]

    def capabilities(self, provider_id: str, account_alias: str | None = None) -> AccountCapabilities:
        _, capabilities, _ = self._resolve(provider_id, account_alias)
        return capabilities

    def fetch_balances(self, provider_id: str, account_alias: str | None = None) -> dict:
        provider, capabilities, lock = self._resolve(provider_id, account_alias)
        if not capabilities.balances:
            raise UnsupportedAccountCapability("Account provider does not declare balance support")
        fetcher = getattr(provider, "fetch_balances", None)
        if not callable(fetcher):
            raise UnsupportedAccountCapability("Account provider declares balances but has no balance reader")
        try:
            with lock:
                raw = fetcher()
        except AccountProviderError:
            raise
        except Exception as exc:
            raise AccountProviderError(f"Account balance request failed ({type(exc).__name__})") from None
        if not isinstance(raw, dict):
            raise AccountProviderError("Account provider returned an invalid balance snapshot")
        if raw.get("provider_id") != capabilities.provider_id:
            raise AccountProviderError("Account provider identity changed in balance response")
        if raw.get("account_alias") != capabilities.account_alias:
            raise AccountProviderError("Account alias changed in balance response")
        market_type = raw.get("market_type")
        if not isinstance(market_type, str) or market_type not in capabilities.market_types:
            raise AccountProviderError("Account market type is not declared by the provider")
        raw_environment = raw.get("environment", capabilities.environment)
        if not isinstance(raw_environment, str) or raw_environment.strip().lower() != capabilities.environment:
            raise AccountProviderError("Account environment conflicts with declared provider capabilities")
        fetched_at = raw.get("fetched_at")
        if not isinstance(fetched_at, str):
            raise AccountProviderError("Account balance timestamp is missing")
        try:
            timestamp = datetime.fromisoformat(fetched_at)
        except ValueError:
            raise AccountProviderError("Account balance timestamp is invalid") from None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise AccountProviderError("Account balance timestamp must include a timezone")
        age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > 300:
            raise AccountProviderError("Account balance snapshot is stale or future-dated")

        flags = {
            "read_only": raw.get("read_only"),
            "execution_enabled": raw.get("execution_enabled"),
            "withdrawals_enabled": raw.get("withdrawals_enabled"),
        }
        if any(not isinstance(value, bool) for value in flags.values()):
            raise AccountProviderError("Account balance permission flags are missing or invalid")
        if flags["read_only"] != capabilities.read_only:
            raise AccountProviderError("Account read-only state conflicts with declared capabilities")
        if flags["execution_enabled"] != capabilities.order_submission:
            raise AccountProviderError("Account execution state conflicts with declared capabilities")
        if flags["withdrawals_enabled"] != capabilities.withdrawals:
            raise AccountProviderError("Account withdrawal state conflicts with declared capabilities")

        raw_balances = raw.get("balances")
        if not isinstance(raw_balances, list):
            raise AccountProviderError("Account balances must be a list")
        balances = []
        seen = set()
        for item in raw_balances:
            balance = AccountBalance.normalize(item)
            if balance.asset in seen:
                raise AccountProviderError("Account provider returned duplicate asset balances")
            seen.add(balance.asset)
            balances.append(balance)

        snapshot = AccountBalanceSnapshot(
            provider_id=capabilities.provider_id,
            account_alias=capabilities.account_alias,
            market_type=market_type,
            environment=capabilities.environment,
            fetched_at=timestamp.astimezone(timezone.utc).isoformat(),
            read_only=flags["read_only"],
            execution_enabled=flags["execution_enabled"],
            withdrawals_enabled=flags["withdrawals_enabled"],
            transfers_enabled=capabilities.transfers,
            wallet_signing_enabled=capabilities.wallet_signing,
            permissions_verified=capabilities.permissions_verified,
            balances=tuple(sorted(balances, key=lambda item: item.asset)),
        ).to_dict()
        for optional in ("demo_only", "coverage_status", "timestamp_source"):
            if optional in raw:
                snapshot[optional] = raw[optional]
        return snapshot

    def fetch_positions(
        self, provider_id: str, account_alias: str | None = None, *, market_type: str | None = None,
    ) -> dict:
        provider, capabilities, lock = self._resolve(provider_id, account_alias)
        if not capabilities.positions:
            raise UnsupportedAccountCapability("Account provider does not declare position support")
        if market_type is None:
            if len(capabilities.market_types) != 1:
                raise InvalidAccountRequest("market_type is required for a multi-market account")
            selected_market = capabilities.market_types[0]
        else:
            selected_market = str(market_type).strip().lower()
            if not selected_market:
                raise InvalidAccountRequest("market_type cannot be blank")
            if selected_market not in capabilities.market_types:
                raise UnsupportedAccountCapability("Requested market_type is not declared by the account")
        fetcher = getattr(provider, "fetch_positions", None)
        if not callable(fetcher):
            raise UnsupportedAccountCapability("Account provider declares positions but has no position reader")
        try:
            with lock:
                raw = fetcher()
        except AccountProviderError:
            raise
        except Exception as exc:
            raise AccountProviderError(f"Account position request failed ({type(exc).__name__})") from None
        if not isinstance(raw, list):
            raise AccountProviderError("Account provider returned an invalid positions collection")
        positions = []
        seen = set()
        for item in raw:
            position = AccountPosition.normalize(item)
            if position.market_type not in capabilities.market_types:
                raise AccountProviderError("Account position market type is not declared by the provider")
            identity = (position.instrument_id, position.market_type, position.side)
            if identity in seen:
                raise AccountProviderError("Account provider returned duplicate positions")
            seen.add(identity)
            if position.market_type == selected_market:
                positions.append(position)
        return AccountPositionSnapshot(
            provider_id=capabilities.provider_id,
            account_alias=capabilities.account_alias,
            market_type=selected_market,
            environment=capabilities.environment,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            positions=tuple(sorted(positions, key=lambda item: (item.instrument_id, item.side))),
            coverage_status="unknown",
        ).to_dict()

    def fetch_positions_snapshot(
        self, provider_id: str, account_alias: str | None = None, *, market_type: str | None = None,
    ) -> dict:
        provider, capabilities, lock = self._resolve(provider_id, account_alias)
        if not capabilities.positions:
            raise UnsupportedAccountCapability("Account provider does not declare position support")
        if not capabilities.read_only or any((
            capabilities.order_management, capabilities.order_submission,
            capabilities.order_cancellation, capabilities.transfers,
            capabilities.withdrawals, capabilities.wallet_signing,
        )):
            raise UnsupportedAccountCapability("Position snapshots require an explicitly read-only account")
        if market_type is None:
            if len(capabilities.market_types) != 1:
                raise InvalidAccountRequest("market_type is required for a multi-market position snapshot")
            selected_market = capabilities.market_types[0]
        else:
            selected_market = str(market_type).strip().lower()
            if not selected_market:
                raise InvalidAccountRequest("market_type cannot be blank")
            if selected_market not in capabilities.market_types:
                raise UnsupportedAccountCapability("Requested market_type is not declared by the account")
        fetcher = getattr(provider, "fetch_positions_snapshot", None)
        if not callable(fetcher):
            raise UnsupportedAccountCapability(
                "Provider must implement fetch_positions_snapshot with explicit coverage metadata"
            )
        try:
            with lock:
                raw = fetcher(market_type=selected_market)
        except AccountProviderError:
            raise
        except Exception as exc:
            raise AccountProviderError(f"Account position snapshot request failed ({type(exc).__name__})") from None
        if not isinstance(raw, dict):
            raise AccountProviderError("Account provider returned an invalid position snapshot")
        if raw.get("provider_id") != capabilities.provider_id:
            raise AccountProviderError("Account provider identity changed in position response")
        if raw.get("account_alias") != capabilities.account_alias:
            raise AccountProviderError("Account alias changed in position response")
        if str(raw.get("environment", "")).strip().lower() != capabilities.environment:
            raise AccountProviderError("Account environment conflicts with declared provider capabilities")
        response_market_type = raw.get("market_type")
        if not isinstance(response_market_type, str) or response_market_type.strip().lower() != selected_market:
            raise AccountProviderError("Position snapshot market_type conflicts with the requested market")
        if raw.get("read_only") is not True or raw.get("execution_enabled") is not False:
            raise AccountProviderError("Position snapshot permission flags conflict with read-only capability")
        if raw.get("withdrawals_enabled") is not False:
            raise AccountProviderError("Position snapshot withdrawals must be disabled")
        coverage = raw.get("coverage_status")
        if coverage not in {"complete", "partial"}:
            raise AccountProviderError("Position provider must declare complete or partial coverage")
        fetched_at = raw.get("fetched_at")
        if not isinstance(fetched_at, str):
            raise AccountProviderError("Position snapshot timestamp is missing")
        try:
            timestamp = datetime.fromisoformat(fetched_at)
        except ValueError:
            raise AccountProviderError("Position snapshot timestamp is invalid") from None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise AccountProviderError("Position snapshot timestamp must include a timezone")
        age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > 300:
            raise AccountProviderError("Position snapshot is stale or future-dated")
        raw_positions = raw.get("positions")
        if not isinstance(raw_positions, list):
            raise AccountProviderError("Position snapshot positions must be a list")
        positions = []
        seen = set()
        for item in raw_positions:
            position = AccountPosition.normalize(item)
            if position.market_type != selected_market:
                raise AccountProviderError("Position market type conflicts with snapshot")
            if position.instrument_id in seen:
                raise AccountProviderError("Position provider returned duplicate instrument positions")
            seen.add(position.instrument_id)
            positions.append(asdict(position))
        return {
            "provider_id": capabilities.provider_id,
            "account_alias": capabilities.account_alias,
            "environment": capabilities.environment,
            "market_type": selected_market,
            "fetched_at": timestamp.astimezone(timezone.utc).isoformat(),
            "read_only": True,
            "execution_enabled": False,
            "withdrawals_enabled": False,
            "coverage_status": coverage,
            "positions": sorted(positions, key=lambda item: item["instrument_id"]),
        }

    def fetch_coordinated_snapshot(
        self, provider_id: str, account_alias: str | None = None, *, market_type: str,
    ) -> tuple[dict, dict]:
        capabilities = self.capabilities(provider_id, account_alias)
        if not capabilities.balances or not capabilities.positions:
            raise UnsupportedAccountCapability(
                "Coordinated snapshots require explicit balance and position capabilities"
            )
        if not capabilities.read_only or any((
            capabilities.order_management, capabilities.order_submission,
            capabilities.order_cancellation, capabilities.transfers,
            capabilities.withdrawals, capabilities.wallet_signing,
        )):
            raise UnsupportedAccountCapability("Coordinated snapshots require a strictly read-only account")
        balances = self.fetch_balances(provider_id, account_alias)
        if balances.get("coverage_status") not in {"complete", "partial"}:
            raise UnsupportedAccountCapability(
                "Balance provider must declare complete or partial snapshot coverage"
            )
        positions = self.fetch_positions_snapshot(
            provider_id, account_alias, market_type=market_type,
        )
        if balances["market_type"] != positions["market_type"]:
            raise AccountProviderError("Balance and position market scopes do not match")
        balances["timestamp_source"] = balances.get("timestamp_source", "provider")
        positions["timestamp_source"] = positions.get("timestamp_source", "provider")
        return balances, positions

    def record_read_only_snapshot(
        self,
        provider_id: str,
        snapshot_store: Any,
        account_alias: str | None = None,
    ) -> dict:
        capabilities = self.capabilities(provider_id, account_alias)
        if not capabilities.read_only or any((
            capabilities.order_management,
            capabilities.order_submission,
            capabilities.order_cancellation,
            capabilities.transfers,
            capabilities.withdrawals,
            capabilities.wallet_signing,
        )):
            raise UnsupportedAccountCapability("Only explicitly read-only accounts can be snapshotted")
        snapshot = self.fetch_balances(provider_id, account_alias)
        if snapshot["market_type"] != "spot":
            raise UnsupportedAccountCapability("Account snapshot store currently supports spot balances only")
        return snapshot_store.record_snapshot(snapshot)

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import re
from threading import RLock
from typing import Any, Protocol, runtime_checkable


class AccountProviderError(RuntimeError):
    pass


class AccountProviderNotFound(AccountProviderError):
    pass


class AmbiguousAccountProvider(AccountProviderError):
    pass


class InvalidAccountRequest(AccountProviderError):
    pass


class UnsupportedAccountCapability(AccountProviderError):
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
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,256}", instrument_id):
            raise AccountProviderError("Account position instrument_id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", symbol):
            raise AccountProviderError("Account position symbol is invalid")
        asset_class = values["asset_class"].lower()
        market_type = values["market_type"].lower()
        side = values["side"].lower()
        quote_currency = values["quote_currency"].upper()
        if asset_class not in {"crypto", "equity", "etf", "forex", "index", "commodity", "future", "option", "bond", "other", "defi", "tokenized"}:
            raise AccountProviderError("Account position asset_class is unsupported")
        if market_type not in {"spot", "margin", "future", "futures", "option", "options", "perpetual", "cash", "cash_equity", "forex", "onchain"}:
            raise AccountProviderError("Account position market_type is unsupported")
        if side not in {"long", "short"}:
            raise AccountProviderError("Account position side is invalid")
        if not re.fullmatch(r"[A-Z0-9._-]{1,24}", quote_currency):
            raise AccountProviderError("Account position quote_currency is invalid")
        quantity = _decimal_text(raw.get("quantity"), field="position quantity", allow_zero=False)
        if quantity is None:
            raise AccountProviderError("Account position quantity is required")
        optional = {}
        for field in ("average_entry_price", "mark_price", "market_value"):
            optional[field] = _decimal_text(raw.get(field), field=f"position {field}")
        for field in ("average_entry_price", "mark_price"):
            if optional[field] is not None and Decimal(optional[field]) <= 0:
                raise AccountProviderError(f"Account position {field} must be positive")
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
    positions_complete: bool
    positions: tuple[AccountPosition, ...]

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_alias": self.account_alias,
            "market_type": self.market_type,
            "environment": self.environment,
            "fetched_at": self.fetched_at,
            "positions_complete": self.positions_complete,
            "positions": [asdict(item) for item in self.positions],
            "position_count": len(self.positions),
        }


@runtime_checkable
class AccountBalanceProvider(Protocol):
    provider_id: str

    def describe(self) -> dict: ...
    def fetch_balances(self) -> dict: ...


@runtime_checkable
class PositionProvider(Protocol):
    def fetch_positions(self, market_type: str | None = None) -> dict: ...


@runtime_checkable
class OrderReconciliationProvider(Protocol):
    def list_open_orders(self) -> list[AccountOrder]: ...
    def fetch_order(self, order_id: str, instrument_id: str) -> AccountOrder | None: ...
    def fetch_fills(self, order_id: str, instrument_id: str) -> list[dict]: ...


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
        has_balance_method = callable(getattr(provider, "fetch_balances", None))
        has_position_method = callable(getattr(provider, "fetch_positions", None))
        if capabilities.balances and not has_balance_method:
            raise AccountProviderError("Provider declares balances but has no fetch_balances method")
        if capabilities.positions and not has_position_method:
            raise AccountProviderError("Provider declares positions but has no fetch_positions method")
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
        try:
            with lock:
                raw = provider.fetch_balances()
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
        for optional in ("demo_only",):
            if optional in raw:
                snapshot[optional] = bool(raw[optional])
        return snapshot

    def fetch_positions(
        self, provider_id: str, account_alias: str | None = None, *, market_type: str | None = None,
    ) -> dict:
        provider, capabilities, lock = self._resolve(provider_id, account_alias)
        if not capabilities.positions:
            raise UnsupportedAccountCapability("Account provider does not declare position support")
        fetcher = getattr(provider, "fetch_positions", None)
        if not callable(fetcher):
            raise UnsupportedAccountCapability("Account provider declares positions but has no position reader")
        try:
            with lock:
                raw = fetcher(market_type=market_type)
        except AccountProviderError:
            raise
        except Exception as exc:
            raise AccountProviderError(f"Account position request failed ({type(exc).__name__})") from None
        if not isinstance(raw, dict):
            raise AccountProviderError("Account provider must return a timestamped position snapshot object")
        if raw.get("provider_id") != capabilities.provider_id:
            raise AccountProviderError("Account provider identity changed in position response")
        if raw.get("account_alias") != capabilities.account_alias:
            raise AccountProviderError("Account alias changed in position response")
        if raw.get("environment") != capabilities.environment:
            raise AccountProviderError("Account environment conflicts with declared provider capabilities")
        selected_market = raw.get("market_type")
        if not isinstance(selected_market, str) or selected_market not in capabilities.market_types:
            raise AccountProviderError("Position market_type is not declared by the provider")
        if market_type is not None and selected_market != market_type:
            raise InvalidAccountRequest("Position market_type conflicts with requested account market")
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
        positions_complete = raw.get("positions_complete", False)
        if not isinstance(positions_complete, bool):
            raise AccountProviderError("Position completeness flag must be a boolean")
        raw_positions = raw.get("positions")
        if not isinstance(raw_positions, list):
            raise AccountProviderError("Account provider returned an invalid positions collection")
        positions = []
        seen = set()
        for item in raw_positions:
            position = AccountPosition.normalize(item)
            if position.market_type != selected_market:
                raise AccountProviderError("Position record market_type conflicts with snapshot")
            identity = (position.instrument_id, position.side)
            if identity in seen:
                raise AccountProviderError("Account provider returned duplicate instrument/side positions")
            seen.add(identity)
            positions.append(position)
        return AccountPositionSnapshot(
            provider_id=capabilities.provider_id,
            account_alias=capabilities.account_alias,
            market_type=selected_market,
            environment=capabilities.environment,
            fetched_at=timestamp.astimezone(timezone.utc).isoformat(),
            positions_complete=positions_complete,
            positions=tuple(sorted(positions, key=lambda item: (item.instrument_id, item.side))),
        ).to_dict()

    def fetch_account_snapshot(
        self, provider_id: str, account_alias: str | None = None, *, market_type: str | None = None,
    ) -> dict:
        capabilities = self.capabilities(provider_id, account_alias)
        if not capabilities.balances and not capabilities.positions:
            raise UnsupportedAccountCapability("Account provider exposes no snapshot data")
        balance_snapshot = self.fetch_balances(provider_id, account_alias) if capabilities.balances else None
        selected_market = balance_snapshot["market_type"] if balance_snapshot is not None else market_type
        if balance_snapshot is not None and market_type is not None and market_type != selected_market:
            raise InvalidAccountRequest("Requested market_type conflicts with the account balance market")
        if selected_market is None:
            if len(capabilities.market_types) != 1:
                raise InvalidAccountRequest("market_type is required for a multi-market account")
            selected_market = capabilities.market_types[0]
        if selected_market not in capabilities.market_types:
            raise UnsupportedAccountCapability("Requested market_type is not declared by this account")
        position_snapshot = (
            self.fetch_positions(provider_id, account_alias, market_type=selected_market)
            if capabilities.positions else None
        )
        timestamps = []
        if balance_snapshot is not None:
            timestamps.append(datetime.fromisoformat(balance_snapshot["fetched_at"]))
        if position_snapshot is not None:
            timestamps.append(datetime.fromisoformat(position_snapshot["fetched_at"]))
        if len(timestamps) == 2 and abs((timestamps[0] - timestamps[1]).total_seconds()) > 60:
            raise AccountProviderError("Balance and position observations are too far apart to form one snapshot")
        fetched_at = min(timestamps).isoformat() if timestamps else datetime.now(timezone.utc).isoformat()
        return {
            "provider_id": capabilities.provider_id,
            "account_alias": capabilities.account_alias,
            "market_type": selected_market,
            "environment": capabilities.environment,
            "venue_type": capabilities.venue_type,
            "fetched_at": fetched_at,
            "read_only": capabilities.read_only,
            "execution_enabled": capabilities.order_submission,
            "withdrawals_enabled": capabilities.withdrawals,
            "transfers_enabled": capabilities.transfers,
            "wallet_signing_enabled": capabilities.wallet_signing,
            "permissions_verified": capabilities.permissions_verified,
            "balances": balance_snapshot["balances"] if balance_snapshot else [],
            "positions_supported": capabilities.positions,
            "positions_complete": position_snapshot["positions_complete"] if position_snapshot else False,
            "positions": position_snapshot["positions"] if position_snapshot else [],
        }
    def record_read_only_snapshot(
        self,
        provider_id: str,
        snapshot_store: Any,
        account_alias: str | None = None,
        market_type: str | None = None,
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
        snapshot = self.fetch_account_snapshot(provider_id, account_alias, market_type=market_type)
        return snapshot_store.record_snapshot(snapshot)

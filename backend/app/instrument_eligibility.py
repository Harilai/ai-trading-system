from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .providers.base import AssetClass, MarketType, PrecisionMode, ProviderCapabilities


@dataclass(frozen=True)
class InstrumentEligibility:
    status: str
    provider_id: str
    catalogue_id: str
    instrument_id: str
    asset_class: str
    market_type: str
    market_data_available: bool
    market_data_authentication_required: bool
    execution_capability: str
    execution_adapter_configured: bool
    missing_requirements: tuple[str, ...]
    blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["missing_requirements"] = list(self.missing_requirements)
        result["blockers"] = list(self.blockers)
        return result


def _finite_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return number > 0 and number < float("inf") and number == number


def evaluate_instrument_eligibility(
    instrument: dict[str, Any],
    capabilities: ProviderCapabilities | None,
    *,
    catalogue_id: str,
    execution_adapter_configured: bool = False,
) -> InstrumentEligibility:
    """Conservative, read-only eligibility assessment. Never submits or authorizes orders."""
    provider_id = str(instrument.get("provider_id") or "unknown")
    asset_class = str(instrument.get("asset_class") or "unknown")
    market_type = str(instrument.get("market_type") or "unknown")
    missing: list[str] = []
    blockers: list[str] = []

    if capabilities is None:
        return InstrumentEligibility(
            status="not_configured",
            provider_id=provider_id,
            catalogue_id=catalogue_id,
            instrument_id=str(instrument.get("instrument_id") or ""),
            asset_class=asset_class,
            market_type=market_type,
            market_data_available=False,
            market_data_authentication_required=False,
            execution_capability="unknown",
            execution_adapter_configured=False,
            missing_requirements=("provider_configuration",),
            blockers=("Provider is not registered in the market-data registry",),
        )

    identity_matches = capabilities.provider_id == provider_id
    if not identity_matches:
        blockers.append("Provider identity does not match the instrument")

    try:
        parsed_asset_class = AssetClass(asset_class)
    except ValueError:
        parsed_asset_class = None
        blockers.append("Instrument asset class is unknown")
    try:
        parsed_market_type = MarketType(market_type)
    except ValueError:
        parsed_market_type = None
        blockers.append("Instrument market type is unknown")

    if parsed_asset_class is not None and parsed_asset_class not in capabilities.asset_classes:
        blockers.append("Provider does not declare support for this asset class")
    if parsed_market_type is not None and parsed_market_type not in capabilities.market_types:
        blockers.append("Provider does not declare support for this market type")

    market_data_available = capabilities.public_market_data
    if not market_data_available:
        missing.append("market_data_access")

    if not capabilities.order_management:
        blockers.append("Provider is data-only or does not declare order management")
        execution_capability = "unsupported"
    else:
        execution_capability = "supported"
        if not any((
            capabilities.supports_market_orders,
            capabilities.supports_limit_orders,
            capabilities.supports_stop_orders,
        )):
            blockers.append("Provider declares no supported order type")
        for flag, label in (
            ("supports_client_order_id", "client order idempotency"),
            ("supports_partial_fills", "partial-fill handling"),
            ("supports_cancel", "order cancellation"),
            ("supports_reconciliation", "execution reconciliation"),
            ("balances", "account balances"),
            ("positions", "account positions"),
        ):
            if not getattr(capabilities, flag):
                blockers.append(f"Provider does not declare {label}")
        if not execution_adapter_configured:
            blockers.append("No execution adapter is registered for this provider")
            execution_capability = "not_configured"

    if instrument.get("active") is not True:
        blockers.append("Instrument is not explicitly active")
    if instrument.get("trading_status") != "active":
        blockers.append("Instrument trading status is not active")

    for field in ("base", "quote", "native_symbol"):
        if not isinstance(instrument.get(field), str) or not instrument[field].strip():
            missing.append(field)

    for field in ("minimum_amount", "minimum_cost"):
        if not _finite_positive(instrument.get(field)):
            missing.append(field)

    precision_mode = instrument.get("precision_mode", PrecisionMode.UNKNOWN.value)
    if precision_mode == PrecisionMode.TICK_SIZE.value:
        for field in ("price_tick", "amount_step"):
            if not _finite_positive(instrument.get(field)):
                missing.append(field)
    elif precision_mode in {
        PrecisionMode.DECIMAL_PLACES.value,
        PrecisionMode.SIGNIFICANT_DIGITS.value,
    }:
        for field in ("price_precision", "amount_precision"):
            value = instrument.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                missing.append(field)
    else:
        missing.append("precision_mode")

    if not _finite_positive(instrument.get("maker_fee")):
        # A zero fee is valid; distinguish it from a missing/non-numeric fee.
        fee = instrument.get("maker_fee")
        if fee != 0:
            missing.append("maker_fee")
    if not _finite_positive(instrument.get("taker_fee")):
        fee = instrument.get("taker_fee")
        if fee != 0:
            missing.append("taker_fee")

    if asset_class in {AssetClass.FUTURE.value, AssetClass.OPTION.value}:
        if not _finite_positive(instrument.get("contract_size")):
            missing.append("contract_size")
        if not isinstance(instrument.get("settlement_currency"), str) or not instrument["settlement_currency"].strip():
            missing.append("settlement_currency")
    if asset_class in {AssetClass.EQUITY.value, AssetClass.ETF.value, AssetClass.INDEX.value}:
        session_timezone = instrument.get("session_timezone")
        if not isinstance(session_timezone, str) or not session_timezone.strip():
            missing.append("session_timezone")
        else:
            try:
                ZoneInfo(session_timezone)
            except (ZoneInfoNotFoundError, ValueError):
                missing.append("valid_session_timezone")

    if asset_class in {AssetClass.FUTURE.value, AssetClass.OPTION.value}:
        expiry = instrument.get("expiry")
        if not isinstance(expiry, str) or not expiry.strip():
            missing.append("expiry")
        else:
            try:
                try:
                    expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                except ValueError:
                    expiry_dt = datetime.combine(date.fromisoformat(expiry), datetime.max.time(), tzinfo=timezone.utc)
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                if expiry_dt <= datetime.now(timezone.utc):
                    blockers.append("Instrument contract has expired")
            except ValueError:
                missing.append("valid_expiry")
        if not _finite_positive(instrument.get("contract_size")):
            if "contract_size" not in missing:
                missing.append("contract_size")
        if not isinstance(instrument.get("contract_type"), str) or not instrument["contract_type"].strip():
            missing.append("contract_type")
        if not isinstance(instrument.get("settlement_currency"), str) or not instrument["settlement_currency"].strip():
            missing.append("settlement_currency")

    if asset_class == AssetClass.OPTION.value:
        if not _finite_positive(instrument.get("strike")):
            missing.append("strike")
        if instrument.get("option_type") not in {"call", "put"}:
            missing.append("option_type")
        if not isinstance(instrument.get("underlying"), str) or not instrument["underlying"].strip():
            missing.append("underlying")

    if blockers:
        hard_unsupported = any(
            marker in item
            for item in blockers
            for marker in (
                "does not declare support",
                "Provider does not declare",
                "Provider declares no supported order type",
                "data-only",
                "has expired",
                "not explicitly active",
                "trading status is not active",
            )
        )
        status = "unsupported" if hard_unsupported else "not_configured"
    elif missing:
        status = "unknown"
    elif execution_capability == "not_configured":
        status = "not_configured"
    elif execution_capability == "supported":
        status = "supported"
    else:
        status = "unsupported"

    return InstrumentEligibility(
        status=status,
        provider_id=provider_id,
        catalogue_id=catalogue_id,
        instrument_id=str(instrument.get("instrument_id") or ""),
        asset_class=asset_class,
        market_type=market_type,
        market_data_available=market_data_available,
        market_data_authentication_required=capabilities.requires_authentication_for_market_data,
        execution_capability=execution_capability,
        execution_adapter_configured=execution_adapter_configured,
        missing_requirements=tuple(sorted(set(missing))),
        blockers=tuple(blockers),
    )

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
        if not isinstance(api_urls, dict):
            raise BinanceDemoError("CCXT Binance REST endpoint map is unavailable")
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, str):
                if value.startswith(("https://", "http://")):
                    found.append(value.lower())
            elif isinstance(value, dict):
                # Some CCXT versions group endpoints by market type. Only inspect
                # the Spot routes used by this adapter, never unrelated futures/SAPI URLs.
                if "spot" in value:
                    visit(value["spot"])
                else:
                    for nested in value.values():
                        visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(api_urls.get("public"))
        visit(api_urls.get("private"))
        if any("api.binance.com" in url and "demo-api.binance.com" not in url for url in found):
            raise BinanceDemoError("Production Binance REST endpoint detected; refusing to initialize")
        if not found or not any("demo-api.binance.com" in url for url in found):
            raise BinanceDemoError("CCXT did not route REST requests to Binance Demo Trading")

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


class BinanceSpotDemoExecution(BinanceSpotDemoAccount):
    """Limit-order execution and reconciliation restricted to Binance Demo Trading."""

    def describe(self) -> dict:
        result = super().describe()
        result.update({
            "order_submission": True,
            "order_management": True,
            "order_types": ["limit"],
            "market_orders": False,
            "max_order_quote_default": "25",
        })
        return result

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        if not isinstance(value, str) or not re.fullmatch(r"\\d+(?:\\.\\d+)?", value):
            raise BinanceDemoError(f"Demo order {field} must be a plain decimal string")
        try:
            result = Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            raise BinanceDemoError(f"Demo order {field} is invalid") from None
        if not result.is_finite() or result <= 0:
            raise BinanceDemoError(f"Demo order {field} must be finite and positive")
        if len(result.as_tuple().digits) > 50 or abs(result.as_tuple().exponent) > 50:
            raise BinanceDemoError(f"Demo order {field} precision is unsupported")
        return result

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text

    @classmethod
    def _nonnegative_decimal_text(cls, value: Any, field: str) -> str | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise BinanceDemoError(f"Exchange returned malformed {field} quantity") from None
        if not parsed.is_finite() or parsed < 0:
            raise BinanceDemoError(f"Exchange returned invalid {field} quantity")
        if len(parsed.as_tuple().digits) > 80 or abs(parsed.as_tuple().exponent) > 80:
            raise BinanceDemoError(f"Exchange returned unsupported {field} precision")
        return cls._canonical_decimal(parsed)

    @staticmethod
    def _market_limit(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise BinanceDemoError("Binance Demo returned malformed market limits") from None
        if not number.is_finite() or number < 0:
            raise BinanceDemoError("Binance Demo returned invalid market limits")
        return number

    def validate_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: str,
        price: str,
        client_order_id: str,
        max_order_quote: str = "25",
    ) -> dict:
        if not isinstance(symbol, str) or not symbol.strip():
            raise BinanceDemoError("Demo order symbol is required")
        symbol = symbol.strip().upper()
        if side not in ("buy", "sell"):
            raise BinanceDemoError("Demo order side must be buy or sell")
        if not isinstance(client_order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise BinanceDemoError("client_order_id must contain 1-36 safe characters")

        amount_value = self._decimal(amount, "amount")
        price_value = self._decimal(price, "price")
        max_quote_value = self._decimal(max_order_quote, "max order quote cap")
        try:
            markets = self._exchange.load_markets()
        except Exception as exc:
            raise BinanceDemoError(
                f"Binance Demo market metadata request failed ({type(exc).__name__})"
            ) from None
        market = markets.get(symbol) if isinstance(markets, dict) else None
        if not isinstance(market, dict) or market.get("spot") is not True:
            raise BinanceDemoError("Order symbol is not a supported Binance Demo Spot market")
        if market.get("active") is not True:
            raise BinanceDemoError("Order symbol is inactive or has unknown status on Binance Demo Spot")
        quote_asset = market.get("quote")
        if not isinstance(quote_asset, str) or not quote_asset:
            raise BinanceDemoError("Binance Demo market is missing quote asset metadata")
        if quote_asset not in {"USDT", "USDC", "FDUSD", "TUSD", "EUR"}:
            raise BinanceDemoError(
                "Initial Demo order guard supports only stablecoin/EUR quote markets"
            )

        amount_formatter = getattr(self._exchange, "amount_to_precision", None)
        price_formatter = getattr(self._exchange, "price_to_precision", None)
        if not callable(amount_formatter) or not callable(price_formatter):
            raise BinanceDemoError("CCXT market precision helpers are unavailable")
        try:
            formatted_amount = Decimal(str(amount_formatter(symbol, str(amount_value))))
            formatted_price = Decimal(str(price_formatter(symbol, str(price_value))))
        except Exception as exc:
            raise BinanceDemoError(
                f"Demo order precision validation failed ({type(exc).__name__})"
            ) from None
        if formatted_amount != amount_value or formatted_price != price_value:
            raise BinanceDemoError("Order amount or price exceeds the market precision")

        limits = market.get("limits") or {}
        if not isinstance(limits, dict):
            raise BinanceDemoError("Binance Demo returned malformed market limits")
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        if not isinstance(amount_limits, dict) or not isinstance(cost_limits, dict):
            raise BinanceDemoError("Binance Demo returned malformed amount/cost limits")
        minimum_amount = self._market_limit(amount_limits.get("min"))
        maximum_amount = self._market_limit(amount_limits.get("max"))
        minimum_cost = self._market_limit(cost_limits.get("min"))
        maximum_cost = self._market_limit(cost_limits.get("max"))
        notional = amount_value * price_value
        if minimum_amount is not None and amount_value < minimum_amount:
            raise BinanceDemoError("Order amount is below the market minimum")
        if maximum_amount is not None and amount_value > maximum_amount:
            raise BinanceDemoError("Order amount exceeds the market maximum")
        if minimum_cost is not None and notional < minimum_cost:
            raise BinanceDemoError("Order notional is below the market minimum")
        if maximum_cost is not None and notional > maximum_cost:
            raise BinanceDemoError("Order notional exceeds the market maximum")
        if notional > max_quote_value:
            raise BinanceDemoError("Order notional exceeds the configured Demo quote cap")

        return {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "amount": self._canonical_decimal(formatted_amount),
            "price": self._canonical_decimal(formatted_price),
            "quote_asset": quote_asset,
            "max_order_quote": self._canonical_decimal(max_quote_value),
        }

    def normalize_order(
        self,
        response: Any,
        *,
        expected_client_order_id: str,
        expected_amount: str | None = None,
        expected_exchange_order_id: str | None = None,
    ) -> dict:
        if not isinstance(response, dict):
            raise BinanceDemoError("Binance Demo returned an invalid order response")
        info = response.get("info") if isinstance(response.get("info"), dict) else {}
        client_id = (
            response.get("clientOrderId")
            or info.get("clientOrderId")
            or info.get("origClientOrderId")
        )
        exchange_order_id = response.get("id")
        if client_id != expected_client_order_id:
            return {
                "status": "reconciliation_required",
                "error": "Exchange order response did not match the immutable client order ID",
            }
        if exchange_order_id is None or not str(exchange_order_id).strip():
            return {
                "status": "reconciliation_required",
                "error": "Exchange response did not contain a durable order ID",
            }
        if (
            expected_exchange_order_id is not None
            and str(exchange_order_id) != str(expected_exchange_order_id)
        ):
            return {
                "status": "reconciliation_required",
                "error": "Exchange order response did not match the persisted exchange order ID",
            }

        raw_status = str(response.get("status") or "").lower()
        raw_filled = response.get("filled")
        filled = self._nonnegative_decimal_text(
            "0" if raw_filled is None else raw_filled, "filled",
        )
        remaining = self._nonnegative_decimal_text(response.get("remaining"), "remaining")
        average_value = self._nonnegative_decimal_text(response.get("average"), "average")
        average = average_value if average_value not in (None, "0") else None
        reported_amount = self._nonnegative_decimal_text(response.get("amount"), "order amount")
        amount_value = (
            self._decimal(expected_amount, "expected amount")
            if expected_amount is not None
            else (Decimal(reported_amount) if reported_amount is not None else None)
        )
        filled_value = Decimal(filled or "0")
        remaining_value = Decimal(remaining) if remaining is not None else None
        inconsistent = False
        if expected_amount is not None and reported_amount is not None:
            inconsistent = Decimal(reported_amount) != amount_value
        if amount_value is not None:
            if filled_value > amount_value:
                inconsistent = True
            if remaining_value is not None and remaining_value > amount_value:
                inconsistent = True
            if remaining_value is not None and filled_value + remaining_value != amount_value:
                inconsistent = True

        if inconsistent:
            status = "reconciliation_required"
        elif raw_status == "closed":
            if (
                filled_value <= 0
                or remaining_value != 0
                or amount_value is None
                or filled_value != amount_value
            ):
                status = "reconciliation_required"
            else:
                status = "filled"
        elif raw_status in ("canceled", "cancelled", "expired"):
            if filled_value > 0 and remaining_value is None:
                status = "reconciliation_required"
            elif amount_value is not None and filled_value == amount_value and remaining_value == 0:
                # The final fill may race with a cancel/expiry response.
                status = "filled"
            else:
                status = "expired" if raw_status == "expired" else "cancelled"
        elif raw_status == "rejected":
            status = "reconciliation_required" if filled_value > 0 else "rejected"
        elif raw_status == "open":
            if filled_value > 0 and remaining_value is None:
                status = "reconciliation_required"
            elif filled_value > 0 and remaining_value == 0:
                status = (
                    "filled"
                    if amount_value is not None and filled_value == amount_value
                    else "reconciliation_required"
                )
            elif filled_value > 0:
                status = "partially_filled"
            elif remaining_value == 0:
                status = "reconciliation_required"
            else:
                status = "open"
        else:
            status = "reconciliation_required"
        return {
            "status": status,
            "exchange_order_id": str(exchange_order_id),
            "exchange_status": raw_status or "unknown",
            "filled": filled,
            "remaining": remaining,
            "average": average,
            "error": (
                None
                if status != "reconciliation_required"
                else "Exchange order status, identity or fill quantities are inconsistent"
            ),
        }

    def submit_limit_order(self, request: dict) -> dict:
        try:
            response = self._exchange.create_order(
                request["symbol"],
                "limit",
                request["side"],
                request["amount"],
                request["price"],
                {
                    "clientOrderId": request["client_order_id"],
                    "timeInForce": "GTC",
                },
            )
        except Exception as exc:
            # The exchange may have accepted the request before the connection failed.
            # Callers must reconcile by client order ID and must never resubmit blindly.
            raise BinanceDemoError(
                f"Binance Demo order submission outcome is unknown ({type(exc).__name__})"
            ) from None
        return self.normalize_order(
            response,
            expected_client_order_id=request["client_order_id"],
            expected_amount=request["amount"],
        )

    def fetch_order_by_client_id(
        self, *, symbol: str, client_order_id: str, expected_amount: str | None = None,
    ) -> dict | None:
        try:
            response = self._exchange.fetch_order(
                None,
                symbol,
                {"origClientOrderId": client_order_id},
            )
        except Exception as exc:
            if type(exc).__name__ in ("OrderNotFound", "OrderNotFoundError"):
                return None
            raise BinanceDemoError(
                f"Binance Demo order reconciliation failed ({type(exc).__name__})"
            ) from None
        return self.normalize_order(
            response,
            expected_client_order_id=client_order_id,
            expected_amount=expected_amount,
        )

    def fetch_order_by_id(
        self, *, symbol: str, exchange_order_id: str,
        client_order_id: str, expected_amount: str | None = None,
    ) -> dict:
        try:
            response = self._exchange.fetch_order(exchange_order_id, symbol)
        except Exception as exc:
            raise BinanceDemoError(
                f"Binance Demo order status request failed ({type(exc).__name__})"
            ) from None
        return self.normalize_order(
            response,
            expected_client_order_id=client_order_id,
            expected_amount=expected_amount,
            expected_exchange_order_id=exchange_order_id,
        )

    def cancel_limit_order(
        self,
        *,
        symbol: str,
        exchange_order_id: str,
        client_order_id: str,
        expected_amount: str | None = None,
    ) -> dict:
        try:
            response = self._exchange.cancel_order(exchange_order_id, symbol)
        except Exception as exc:
            raise BinanceDemoError(
                f"Binance Demo cancel outcome is unknown ({type(exc).__name__})"
            ) from None
        return self.normalize_order(
            response,
            expected_client_order_id=client_order_id,
            expected_amount=expected_amount,
            expected_exchange_order_id=exchange_order_id,
        )

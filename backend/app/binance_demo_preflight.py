"""Read-only Binance Spot Demo preflight; this module never submits or mutates orders."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class PreflightError(RuntimeError):
    pass


def _validate_api_url(api_url: str, *, allow_remote: bool = False) -> str:
    parsed = urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PreflightError("API URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PreflightError("API URL must not contain credentials, query parameters or fragments")
    host = parsed.hostname.lower()
    is_loopback = host == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if not is_loopback and not allow_remote:
        raise PreflightError("Remote API hosts require the explicit --allow-remote flag")
    if not is_loopback and parsed.scheme != "https":
        raise PreflightError("Remote preflight requires HTTPS to protect the owner token")
    return api_url.rstrip("/")


def _get_json(api_url: str, path: str, token: str, *, opener=urlopen) -> dict:
    request = Request(
        f"{api_url}{path}",
        headers={"X-Agent-Token": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise PreflightError(f"GET {path} returned HTTP {exc.code}") from None
    except URLError:
        raise PreflightError(f"GET {path} could not reach the API") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PreflightError(f"GET {path} returned invalid JSON") from None
    except TimeoutError:
        raise PreflightError(f"GET {path} timed out") from None
    if not isinstance(payload, dict):
        raise PreflightError(f"GET {path} returned an invalid response object")
    return payload


def validate_preflight(accounts: dict, balances: dict, markets: dict) -> dict:
    configured = accounts.get("accounts")
    if not isinstance(configured, list):
        raise PreflightError("Account summary is missing its accounts list")
    demo = next(
        (item for item in configured if isinstance(item, dict)
         and item.get("provider_id") == "ccxt:binance-demo"
         and item.get("account_alias") == "binance-spot-demo"),
        None,
    )
    if not isinstance(demo, dict):
        raise PreflightError("Binance Spot Demo account is not configured")
    if demo.get("environment") != "demo" or demo.get("demo_mode") is not True or demo.get("demo_only") is not True:
        raise PreflightError("Account capability does not positively identify isolated Demo mode")
    if demo.get("withdrawals") is not False or demo.get("transfers") is not False:
        raise PreflightError("Demo account advertises an unsafe transfer or withdrawal capability")
    if balances.get("environment") != "demo" or balances.get("demo_only") is not True:
        raise PreflightError("Balance response is not positively identified as Demo-only")
    if balances.get("withdrawals_enabled") is not False:
        raise PreflightError("Demo balance response does not explicitly disable withdrawals")
    balance_items = balances.get("balances")
    if not isinstance(balance_items, list):
        raise PreflightError("Demo balance response has no valid balances list")
    if markets.get("environment") != "demo" or markets.get("demo_only") is not True:
        raise PreflightError("Market catalogue is not positively identified as Demo-only")
    market_items = markets.get("markets")
    if not isinstance(market_items, list) or not market_items:
        raise PreflightError("Demo market catalogue is empty or malformed")
    symbols = [item.get("symbol") for item in market_items if isinstance(item, dict)]
    if len(symbols) != len(market_items) or any(not isinstance(symbol, str) or not symbol for symbol in symbols):
        raise PreflightError("Demo market catalogue contains malformed symbols")
    if len(set(symbols)) != len(symbols):
        raise PreflightError("Demo market catalogue contains duplicate symbols")
    return {
        "account": "ccxt:binance-demo / binance-spot-demo",
        "environment": "demo",
        "balance_assets": len(balance_items),
        "active_spot_markets": len(market_items),
        "order_submission_performed": False,
        "exchange_mutations_performed": False,
    }


def run_preflight(
    api_url: str,
    token: str,
    *,
    allow_remote: bool = False,
    opener=urlopen,
) -> dict:
    base = _validate_api_url(api_url, allow_remote=allow_remote)
    if not isinstance(token, str) or not token.strip():
        raise PreflightError("AGENT_CONTROL_TOKEN is required in the environment")
    accounts = _get_json(base, "/api/accounts", token.strip(), opener=opener)
    balances = _get_json(base, "/api/demo/binance/spot/balances", token.strip(), opener=opener)
    markets = _get_json(base, "/api/demo/binance/spot/markets?limit=100", token.strip(), opener=opener)
    return validate_preflight(accounts, balances, markets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Binance Spot Demo preflight. Never creates, cancels or reconciles orders."
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Local API URL (default: %(default)s)")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a remote HTTPS API URL")
    args = parser.parse_args(argv)
    try:
        result = run_preflight(args.api, os.environ.get("AGENT_CONTROL_TOKEN", ""), allow_remote=args.allow_remote)
    except PreflightError as exc:
        print(f"PRE-FLIGHT FAILED: {exc}", file=sys.stderr)
        return 1
    print("PRE-FLIGHT PASSED")
    for key, value in result.items():
        print(f"{key}: {value}")
    print("No orders were submitted, cancelled or reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

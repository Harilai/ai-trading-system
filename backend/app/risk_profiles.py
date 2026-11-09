from enum import Enum


class RiskProfileName(str, Enum):
    SPOT = "spot"
    MARGIN = "margin"
    FUTURES = "futures"
    OPTIONS = "options"
    FOREX = "forex"
    DEFI = "defi"


RISK_PROFILES = {
    RiskProfileName.SPOT: {
        "enabled": True,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["cash_only", "max_order_fraction", "stop_loss", "fees", "slippage"],
    },
    RiskProfileName.MARGIN: {
        "enabled": False,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["margin_ratio", "borrow_cost", "liquidation_price", "counterparty_risk"],
    },
    RiskProfileName.FUTURES: {
        "enabled": False,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["contract_specification", "initial_margin", "maintenance_margin", "liquidation", "funding", "expiry"],
    },
    RiskProfileName.OPTIONS: {
        "enabled": False,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["premium_at_risk", "greeks", "expiry", "exercise_style", "liquidity"],
    },
    RiskProfileName.FOREX: {
        "enabled": False,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["pip_value", "lot_size", "rollover", "margin", "counterparty_risk"],
    },
    RiskProfileName.DEFI: {
        "enabled": False,
        "max_leverage": 1.0,
        "shorting": False,
        "controls": ["gas", "slippage", "contract_audit", "simulation", "MEV", "bridge_risk", "signing"],
    },
}


def risk_profile(name: RiskProfileName) -> dict:
    profile = RISK_PROFILES[name]
    return {**profile, "controls": list(profile["controls"])}

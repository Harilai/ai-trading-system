# Normalized instrument contract v2

## Purpose

The provider-neutral instrument record is the common identity and constraint description consumed by market discovery, research, paper simulation, risk and future execution adapters. It must preserve the provider's actual facts and must not manufacture missing precision, settlement or contract data.

## Core identity

- `provider_id`: configured adapter identity (for example `ccxt:kraken` or `twelve_data`).
- `venue_id`: provider-supplied venue/exchange/MIC identity when available.
- `symbol`: normalized provider symbol used by existing routes and persistence.
- `native_symbol`: original provider-native symbol; defaults to `symbol`.
- `asset_class` and `market_type`: distinct classifications (for example ETF + cash equity).
- `base` and `quote`: instrument currencies/assets; never assume quote currency is EUR.
- Existing `instrument_id = provider_id:market_type:symbol` remains stable for backwards compatibility. A future migration is required before introducing a different canonical key for multiple listings of the same ticker on one data provider.

## Trading constraints

- Minimum/maximum amount and cost.
- Maker/taker fee values.
- `precision_mode` disambiguates how precision numbers are interpreted:
  - `tick_size`: use `price_tick` and `amount_step`.
  - `decimal_places`: use `price_precision` and `amount_precision` as decimal-place counts.
  - `significant_digits`: precision fields are significant-digit counts, not decimal places.
  - `unknown`: do not infer a step or precision.
- Tick size, amount step and contract size must be finite and positive. Limits must be finite and non-negative, with maximum not below minimum.
- Missing constraints remain `null`; consumers must not infer permissive defaults.

## Contract-specific metadata

- `contract_type`: provider-declared spot, cash, future, perpetual, option, or other contract family.
- `contract_size`: multiplier per contract, only when the provider supplies it.
- `settlement_currency`: settlement currency, separate from quote currency.
- `expiry`, `strike`, `option_type`, `underlying`: explicit derivative identity. Options require an ISO expiry, positive strike, call/put right and underlying.
- `trading_status`: active, inactive, suspended, expired or unknown.
- `session_timezone`: exchange session timezone when supplied.

## Current adapters

- CCXT remains public crypto spot market data, EUR-quoted only. The adapter now preserves CCXT precision mode, tick/step or decimal precision, amount/cost bounds, venue ID, and provider-declared contract metadata.
- Twelve Data remains a read-only US equity/ETF market-data adapter. It now preserves MIC/exchange venue identity and native symbol. It does not invent order precision, tick size, lot size, settlement currency or authenticated account capabilities.

## Safety and non-goals

- This contract expansion does not add private exchange authentication, order entry, balances, wallets, signing, DEX, margin, futures or options trading.
- A derivative-shaped instrument record is not evidence that a derivative adapter exists or that the account is eligible.
- A market catalogue entry never implies execution capability.
- Before any paper or live order, consumers must validate market type, precision, min/max limits, contract multiplier, quote/settlement currencies, session and provider capabilities.
- Missing or ambiguous constraints fail closed for execution. Research may still display an instrument as discovery-only with its unknown fields visible.

## Next work

Build a persistent instrument catalogue with discovery timestamps, provider failure records, filters and stable venue-aware identity migration. Then add one market class/venue adapter at a time, beginning with paper/sandbox validation.

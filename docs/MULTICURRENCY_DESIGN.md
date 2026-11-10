# Paper multi-currency portfolio — design

## Scope

Add a paper-only USD cash and US cash-equity/ETF accounting path while retaining EUR as the portfolio reporting/base currency. Twelve Data remains a market-data/FX provider only. This phase does not add a broker, autonomous equity selection, live orders, wallet connectivity or withdrawals in USD.

## Supported first slice

- Existing crypto spot markets quoted in EUR remain unchanged.
- US equities and ETFs discovered by the configured Twelve Data provider are USD-quoted cash instruments.
- The portfolio maintains separate EUR and USD cash balances.
- EUR/USD conversion is an explicit paper transaction. A buy in USD cannot implicitly debit EUR.
- Equity/ETF manual paper orders require the authenticated owner control token, a fresh quote, an open/known exchange session, an explicit stop-loss for buys and deterministic Risk Manager approval.
- The autonomous Chief/decision cycle remains crypto spot EUR only until a later phase explicitly validates and enables equities.

## Currency and valuation model

- Base/reporting currency: EUR.
- Native instrument accounting: the quote currency of the instrument (EUR or USD).
- Cash balances: one row per currency; EUR remains mirrored in the legacy `account.cash` field during migration for backward compatibility.
- Position native average cost remains in quote currency. A second EUR cost basis per unit is maintained using the FX rate at each entry fill, so realised P/L can be reported consistently in EUR.
- Each USD fill stores the USD-to-EUR conversion rate and source timestamp used to establish its EUR cost/P&L basis.
- Portfolio snapshots expose native cash balances and native position values, plus consolidated EUR equity, market value, exposure and P/L. If any required quote or FX rate is missing/stale/invalid, the snapshot marks valuation incomplete and does not silently substitute an invented conversion.

## FX data contract

Twelve Data `/exchange_rate` is the initial FX source. The adapter must:
- Use the existing persistent quota reservation and response-credit reconciliation.
- Cache rates briefly to avoid unnecessary credits.
- Return pair, positive finite rate, provider timestamp (normalised to milliseconds), provider ID and retrieval metadata.
- Reject missing, malformed, non-finite, non-positive, future or stale observations.
- Fail closed for conversion and USD execution if a valid rate is unavailable.
- Keep FX market data read-only; it does not execute currency trades.

The initial conversion pair is EUR/USD. Inverse USD/EUR valuation may be derived from the same validated EUR/USD observation, with the same timestamp and explicit inversion metadata, to avoid a second API credit.

## Paper FX conversion

A conversion request is owner-authenticated and idempotent:
- Source currency and target currency must be distinct supported currencies (initially EUR/USD).
- Source amount, rate, fee rate and destination amount are finite and positive; fee rate is bounded.
- Both currency balances are debited/credited atomically in one SQLite transaction.
- The conversion record persists its idempotency key, currencies, source amount, gross destination amount, fee, net destination amount, rate, FX source/provider, source timestamp and creation timestamp.
- Replaying an idempotency key returns the original conversion without moving cash again.
- No automatic conversion occurs as a side effect of an order.
- Initial simulation assumptions are configurable: 0.10% FX conversion fee, 0.10% equity fee, 0.10% equity slippage, 2% maximum spread when bid/ask exists, 300-second FX freshness, USD 1 minimum notional, 10% maximum order allocation, 20% maximum instrument concentration and 5% maximum stop distance. These are paper-model assumptions, not a broker tariff.

## Equity/ETF paper risk gate

The first equity route is manual-owner initiated only. Risk Manager must independently verify:
- Asset class is equity or ETF, market type is cash equity, provider is Twelve Data, quote currency is USD and instrument identity matches provider discovery.
- Buy is long-only, has a stop-loss below the reference price, and its stop distance is within the configured limit.
- The proposed USD notional plus costs, converted to EUR using a fresh FX observation, stays within the portfolio allocation limit.
- Sufficient USD cash exists after pending reservations; EUR cash is not counted as USD.
- Sells cannot exceed the exact provider/instrument position.
- Market data and exchange session are fresh and valid. Use bid/ask when the provider supplies both; otherwise use the validated last price with explicit conservative paper slippage. Reject malformed or excessively wide bid/ask spreads.

No equity proposal enters the autonomous Chief cycle in this phase. Equity stop-loss/take-profit values are persisted as protective metadata but are not automatically monitored by the worker yet; positions must be closed manually until equity monitoring is implemented. No margin, shorting, options, futures, FX trading, broker adapter or live execution is enabled.

## Persistence and migration

Migration must be additive and transactional:
- Seed the EUR cash-balance row from the existing account cash exactly once.
- Preserve all legacy EUR positions, trades, withdrawals and IDs.
- Add native quote-currency and EUR-basis/FX metadata to positions and trades with safe EUR defaults for existing records.
- Add currency-conversion ledger with a unique idempotency key.
- Keep existing API fields and EUR-only withdrawal semantics compatible.

Migration tests must cover both an empty database and a populated legacy database. Reopening the database must not reseed balances or duplicate conversions.

## Acceptance and failure tests

1. Existing crypto EUR buy/sell, P/L, withdrawal, migration and idempotency tests continue to pass.
2. EUR and USD balances are isolated; USD purchases cannot consume EUR without a prior conversion.
3. EUR/USD conversion is atomic, auditable and idempotent; insufficient funds and duplicate keys cannot double-spend.
4. FX stale, future, malformed, missing, zero, negative, NaN and infinity rates fail closed.
5. USD position cost basis and realised/unrealised P/L are converted to EUR using persisted, timestamped FX evidence.
6. Incomplete price or FX data yields an explicitly incomplete portfolio valuation and blocks new autonomous/manual orders until valuation recovers.
7. Equity/ETF orders are restricted to verified Twelve Data US cash instruments and manual owner authorization; all other unsupported asset classes/providers are rejected.
8. Risk checks include FX-converted EUR exposure and USD cash availability; no shorting or implicit currency conversion.
9. Full test suite and CI pass on the exact PR head SHA before merge.

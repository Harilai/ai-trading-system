# Provider and instrument fee schedules

## Purpose

Strategy Research may use a provider-published fee schedule only when its provenance, scope and validity are explicit. The existing configured `fee_rate` remains a clearly labelled assumption when no verified schedule is supplied.

## Scope and execution role

A schedule is bound to the exact `provider_id`, canonical `instrument_id` and `market_type`. A schedule for one venue, instrument or market cannot be reused for another.

Current Strategy Research fills are simulated as marketable next-bar-open orders. The fee model therefore applies the **taker** rate. Maker rates are retained for provenance and future passive-order research, but are never substituted for taker rates.

## Currency handling

The current percentage-cost backtester supports only fees denominated in the instrument's quote currency. Base-currency or third-token fees are rejected rather than silently converted, ignored or valued using an unrelated FX rate. A future extension must use timestamp-aligned, auditable conversion observations and preserve the original fee asset and amount.

## Time validity and look-ahead

A supplied schedule must include:
- source/provenance identifier;
- local observation timestamp;
- effective-from and effective-until timestamps;
- maker and taker rates;
- fee currency and quote currency;
- exact provider, instrument and market identity.

The schedule must have been observed no later than the first candle in the research sample and must cover the complete sample through its final simulated execution timestamp. The resolver fails closed otherwise. This deliberately conservative single-schedule contract avoids applying today's fee tier retrospectively. Historical tier changes require a future time-segmented fee history with point-in-time selection.

## Current limitations

- No provider fee schedule is fetched or persisted automatically in this phase.
- No private account credentials or account-specific VIP/volume tiers are queried.
- The caller must supply independently verified schedule evidence; absence keeps the configured assumption and is reported as such.
- Fee tiers, maker rebates, third-token discounts and account-specific negotiated rates are not inferred.
- This does not change execution routing, order placement, Risk Manager behavior or LIVE availability.

## Validation

Unit tests cover exact scope, taker selection, quote-currency restriction, invalid rates, schedule observation time and complete-period validity. Strategy Research integration tests verify that the resolved taker rate affects the cost model and is reported with provenance.

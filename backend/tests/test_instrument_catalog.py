from datetime import datetime, timedelta, timezone

from app.instrument_catalog import InstrumentCatalog
from app.providers.base import AssetClass, MarketInstrument, MarketType


def make_equity(*, venue="XNAS", active=True, name="Example Inc", metadata=None):
    return MarketInstrument(
        provider_id="twelve_data",
        symbol="ACME",
        asset_class=AssetClass.EQUITY,
        market_type=MarketType.CASH_EQUITY,
        base="ACME",
        quote="USD",
        active=active,
        venue_id=venue,
        native_symbol="ACME",
        trading_status="active" if active else "suspended",
        metadata={"name": name, **(metadata or {})},
    )


def test_catalogue_uses_venue_aware_identity_without_changing_legacy_id(tmp_path):
    catalog = InstrumentCatalog(str(tmp_path / "catalogue.db"))
    nasdaq = make_equity(venue="XNAS")
    nyse = make_equity(venue="XNYS")

    result = catalog.record_discovery(
        [nasdaq, nyse],
        discovered_at=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["inserted_count"] == 2
    assert catalog.catalogue_id(nasdaq) != catalog.catalogue_id(nyse)
    assert nasdaq.instrument_id == nyse.instrument_id

    items = catalog.list_instruments(provider_id="twelve_data")
    assert len(items) == 2
    assert {item["venue_id"] for item in items} == {"XNAS", "XNYS"}
    assert {item["legacy_instrument_id"] if "legacy_instrument_id" in item else item["instrument_id"] for item in items} == {
        "twelve_data:cash_equity:ACME"
    }


def test_catalogue_upsert_preserves_first_seen_and_updates_last_seen(tmp_path):
    catalog = InstrumentCatalog(str(tmp_path / "catalogue.db"))
    first = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    later = first + timedelta(hours=2)

    initial = catalog.record_discovery([make_equity(name="Old Name")], discovered_at=first)
    updated = catalog.record_discovery([make_equity(name="New Name", active=False)], discovered_at=later)

    assert initial["inserted_count"] == 1
    assert updated["inserted_count"] == 0
    assert updated["updated_count"] == 1
    item = catalog.get_instrument(catalog.catalogue_id(make_equity()))
    assert item["first_seen_at"] == first.isoformat()
    assert item["last_seen_at"] == later.isoformat()
    assert item["metadata"]["name"] == "New Name"
    assert item["active"] is False
    assert item["trading_status"] == "suspended"


def test_catalogue_records_partial_provider_failures_without_deleting_old_records(tmp_path):
    catalog = InstrumentCatalog(str(tmp_path / "catalogue.db"))
    stamp = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    catalog.record_discovery([make_equity()], discovered_at=stamp)

    result = catalog.record_discovery(
        [],
        [{"provider_id": "ccxt:kraken", "reason": "api_key=topsecret connection timeout"}],
        discovered_at=stamp + timedelta(minutes=5),
    )

    assert result["status"] == "failed"
    assert result["failure_count"] == 1
    assert "topsecret" not in result["failures"][0]["reason"]
    assert len(catalog.list_instruments()) == 1
    run = catalog.discovery_runs(1)[0]
    assert run["status"] == "failed"
    assert run["failures"][0]["provider_id"] == "ccxt:kraken"


def test_catalogue_skips_non_json_provider_metadata_and_records_rejection(tmp_path):
    catalog = InstrumentCatalog(str(tmp_path / "catalogue.db"))
    invalid = make_equity(metadata={"invalid_numeric": float("nan")})

    result = catalog.record_discovery([invalid])

    assert result["status"] == "failed"
    assert result["discovered_count"] == 0
    assert result["failure_count"] == 1
    assert "could not be serialized" in result["failures"][0]["reason"]
    assert catalog.list_instruments() == []


def test_catalogue_filters_and_paginates_persisted_instruments(tmp_path):
    catalog = InstrumentCatalog(str(tmp_path / "catalogue.db"))
    instruments = [
        make_equity(venue="XNAS"),
        make_equity(venue="XNYS"),
        MarketInstrument(
            provider_id="ccxt:kraken", symbol="BTC/EUR",
            asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
            base="BTC", quote="EUR", active=True,
        ),
    ]
    catalog.record_discovery(instruments)

    equities = catalog.list_instruments(asset_class="equity", limit=1, offset=0)
    crypto = catalog.list_instruments(asset_class="crypto")
    assert len(equities) == 1
    assert len(crypto) == 1
    assert crypto[0]["provider_id"] == "ccxt:kraken"


def test_catalogue_persists_across_instances_and_tracks_run_timestamps(tmp_path):
    path = str(tmp_path / "catalogue.db")
    catalog = InstrumentCatalog(path)
    started = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    completed = started + timedelta(seconds=5)

    result = catalog.record_discovery(
        [make_equity()],
        started_at=started,
        discovered_at=completed,
    )
    reloaded = InstrumentCatalog(path)

    assert len(reloaded.list_instruments()) == 1
    run = reloaded.discovery_runs(1)[0]
    assert run["run_id"] == result["run_id"]
    assert run["started_at"] == started.isoformat()
    assert run["completed_at"] == completed.isoformat()

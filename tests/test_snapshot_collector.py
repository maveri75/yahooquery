# stdlib
from datetime import datetime, timezone

# first party
from yahooquery.snapshot_collector import (
    CollectorConfig,
    SnapshotCollector,
    select_monthly_expirations,
    should_capture_skew,
)


def utc_timestamp(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class FakeTicker:
    EXPIRATIONS = [
        utc_timestamp(2026, 3, 4),
        utc_timestamp(2026, 3, 18),
        utc_timestamp(2026, 4, 15),
    ]

    def __init__(self, symbols, **kwargs):
        if isinstance(symbols, str):
            self._symbols = symbols.split()
        else:
            self._symbols = list(symbols)
        self.kwargs = kwargs

    @property
    def quotes(self):
        return {
            symbol: {
                "symbol": symbol,
                "regularMarketPrice": 100.0,
                "bid": 99.9,
                "ask": 100.1,
            }
            for symbol in self._symbols
        }

    def _get_data(self, key, params=None):
        assert key == "options"
        symbol = self._symbols[0]
        params = params or {}
        if params.get("date"):
            expiration = int(params["date"])
            call_contract = {
                "contractSymbol": f"{symbol.replace('^', '')}{expiration}C",
                "expiration": expiration,
                "lastTradeDate": expiration - 3600,
                "strike": 10.0,
                "bid": 1.0,
                "ask": 1.2,
            }
            put_contract = {
                "contractSymbol": f"{symbol.replace('^', '')}{expiration}P",
                "expiration": expiration,
                "lastTradeDate": expiration - 3600,
                "strike": 10.0,
                "bid": 0.9,
                "ask": 1.1,
            }
            return {
                symbol: {
                    "expirationDates": [expiration],
                    "options": [{"calls": [call_contract], "puts": [put_contract]}],
                }
            }
        return {
            symbol: {
                "expirationDates": self.EXPIRATIONS,
                "options": [],
            }
        }


class FakeTickerFactory:
    def __init__(self):
        self.instances = []

    def __call__(self, symbols, **kwargs):
        instance = FakeTicker(symbols, **kwargs)
        self.instances.append(instance)
        return instance


def test_select_monthly_expirations_chooses_first_expiration_each_month():
    as_of = datetime(2026, 1, 10, tzinfo=timezone.utc)
    expirations = [
        utc_timestamp(2026, 1, 17),
        utc_timestamp(2026, 1, 24),
        utc_timestamp(2026, 2, 21),
        utc_timestamp(2026, 3, 21),
        utc_timestamp(2026, 4, 18),
    ]
    selected = select_monthly_expirations(expirations, as_of_utc=as_of, lookahead_months=3)
    assert selected == [
        utc_timestamp(2026, 1, 17),
        utc_timestamp(2026, 2, 21),
        utc_timestamp(2026, 3, 21),
    ]


def test_should_capture_skew_only_once_per_day_after_close():
    pre_close = datetime(2026, 2, 25, 15, 59)
    post_close = datetime(2026, 2, 25, 16, 5)
    weekend = datetime(2026, 2, 28, 16, 30)
    assert not should_capture_skew(pre_close, None, 16, 5)
    assert should_capture_skew(post_close, None, 16, 5)
    assert not should_capture_skew(post_close, post_close.date(), 16, 5)
    assert not should_capture_skew(weekend, None, 16, 5)


def test_run_cycle_writes_partitioned_snapshots(tmp_path):
    factory = FakeTickerFactory()
    config = CollectorConfig(
        output_dir=tmp_path,
        spot_symbols=["^SPX", "^VIX"],
        options_symbols=["^SPX"],
        skew_symbol="^SKEW",
        refresh_expirations_hours=24,
        retries=0,
    )
    collector = SnapshotCollector(config=config, ticker_factory=factory, sleep_fn=lambda _: None)
    cycle_time_utc = datetime(2026, 2, 25, 21, 10, tzinfo=timezone.utc)
    metrics = collector.run_cycle(cycle_time_utc)
    metrics["duration_seconds"] = 0.25
    collector._append_metrics(cycle_time_utc.date(), metrics)

    base = tmp_path / "2026-02-25"
    spot_files = list((base / "spot").rglob("snapshot_*.json"))
    option_files = list((base / "options").rglob("snapshot_*.csv"))
    skew_files = list((base / "daily_close").rglob("snapshot_*.json"))
    metrics_file = base / "metrics" / "cycle_metrics.csv"

    assert len(spot_files) == 2
    assert len(option_files) == 2
    assert len(skew_files) == 1
    assert metrics_file.exists()

    assert metrics["spot_snapshots_written"] == 2
    assert metrics["option_api_calls"] == 2
    assert metrics["option_snapshots_written"] == 2
    assert metrics["option_contracts"] == 4
    assert metrics["skew_snapshots_written"] == 1
    assert metrics["errors"] == []

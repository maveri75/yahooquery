# stdlib
import os
import time
from datetime import datetime, timezone

# third party
import pytest

# first party
from yahooquery.snapshot_collector import CollectorConfig, SnapshotCollector

pytestmark = pytest.mark.integration

if os.getenv("YQ_RUN_INTEGRATION") != "1":
    pytest.skip(
        "Integration test disabled. Set YQ_RUN_INTEGRATION=1 to enable.",
        allow_module_level=True,
    )


def test_snapshot_collector_single_cycle_smoke(tmp_path):
    config = CollectorConfig(
        output_dir=tmp_path,
        spot_symbols=["^SPX", "^VIX", "^VVIX", "SPY"],
        options_symbols=["^VIX"],
        retries=1,
        backoff_seconds=0.5,
        timeout=8.0,
        timeout_connect=4.0,
        timeout_read=8.0,
    )
    collector = SnapshotCollector(config=config)

    start = time.monotonic()
    metrics = collector.run_cycle(datetime.now(timezone.utc))
    elapsed = time.monotonic() - start

    # Guardrail: one cycle must stay comfortably below 1-minute cadence.
    assert elapsed < 30
    assert metrics["spot_snapshots_written"] >= 1
    assert metrics["option_api_calls"] >= 1
    assert metrics["option_snapshots_written"] >= 1

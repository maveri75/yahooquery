# stdlib
import argparse
import csv
import json
import logging
import time
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

# third party
import pandas as pd

# first party
from yahooquery.ticker import Ticker

logger = logging.getLogger(__name__)

DEFAULT_SPOT_SYMBOLS = ["^SPX", "^VIX", "^VVIX", "SVOL", "VXX", "UVIX", "UVXY", "SPY"]
DEFAULT_OPTIONS_SYMBOLS = ["^SPX", "^VIX"]
DEFAULT_SKEW_SYMBOL = "^SKEW"


def parse_symbol_list(raw_value: str) -> list[str]:
    values = []
    for token in raw_value.replace(",", " ").split():
        value = token.strip()
        if value:
            values.append(value)
    return values


def add_months(base_date: date, months: int) -> date:
    month_idx = base_date.month - 1 + months
    year = base_date.year + month_idx // 12
    month = month_idx % 12 + 1
    last_day = monthrange(year, month)[1]
    day = min(base_date.day, last_day)
    return date(year, month, day)


def select_monthly_expirations(
    expiration_timestamps: Iterable[int],
    as_of_utc: datetime,
    lookahead_months: int = 12,
) -> list[int]:
    as_of_date = as_of_utc.date()
    horizon_date = add_months(as_of_date, lookahead_months)
    unique_expirations = sorted({int(value) for value in expiration_timestamps})
    monthly_map: dict[tuple[int, int], int] = {}

    for expiration in unique_expirations:
        expiration_date = datetime.fromtimestamp(expiration, tz=timezone.utc).date()
        if expiration_date < as_of_date or expiration_date > horizon_date:
            continue
        month_key = (expiration_date.year, expiration_date.month)
        monthly_map.setdefault(month_key, expiration)

    return [monthly_map[key] for key in sorted(monthly_map)]


def should_capture_skew(
    now_local: datetime,
    last_capture_date: Optional[date],
    capture_hour: int,
    capture_minute: int,
) -> bool:
    if now_local.weekday() >= 5:
        return False
    if last_capture_date == now_local.date():
        return False
    return (now_local.hour, now_local.minute) >= (capture_hour, capture_minute)


def options_to_dataframe(option_groups: list[dict], symbol: str) -> pd.DataFrame:
    frames = []
    for option_type in ["calls", "puts"]:
        option_frames = []
        for option_data in option_groups:
            if not isinstance(option_data, dict):
                continue
            contracts = option_data.get(option_type)
            if contracts:
                data = pd.DataFrame(contracts)
                data["optionType"] = option_type
                option_frames.append(data)
        if option_frames:
            frames.append(pd.concat(option_frames, sort=False))

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, sort=False)
    if "expiration" in df.columns:
        df["expiration"] = pd.to_datetime(df["expiration"], unit="s", errors="coerce")
    if "lastTradeDate" in df.columns:
        df["lastTradeDate"] = pd.to_datetime(
            df["lastTradeDate"], unit="s", errors="coerce"
        )
    df["symbol"] = symbol
    return df


@dataclass
class CollectorConfig:
    output_dir: Path = field(default_factory=lambda: Path("data/raw_snapshots"))
    interval_seconds: int = 60
    lookahead_months: int = 12
    refresh_expirations_hours: int = 24
    retries: int = 2
    backoff_seconds: float = 0.5
    timeout: float = 5.0
    max_workers: int = 8
    market_timezone: str = "America/New_York"
    skew_capture_hour: int = 16
    skew_capture_minute: int = 5
    spot_symbols: list[str] = field(default_factory=lambda: DEFAULT_SPOT_SYMBOLS.copy())
    options_symbols: list[str] = field(
        default_factory=lambda: DEFAULT_OPTIONS_SYMBOLS.copy()
    )
    skew_symbol: str = DEFAULT_SKEW_SYMBOL


class SnapshotCollector:
    def __init__(
        self,
        config: CollectorConfig,
        ticker_factory: Callable[..., Ticker] = Ticker,
        now_fn: Optional[Callable[[timezone], datetime]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self._ticker_factory = ticker_factory
        self._now_fn = now_fn or (lambda tz: datetime.now(tz))
        self._sleep_fn = sleep_fn
        self._market_tz = ZoneInfo(self.config.market_timezone)

        self._expiration_cache: dict[str, list[int]] = {}
        self._expiration_cache_updated_at: Optional[datetime] = None
        self._skew_last_capture_date: Optional[date] = None

        self.spot_ticker = self._create_ticker(
            self.config.spot_symbols,
            asynchronous=True,
            timeout=self.config.timeout,
            max_workers=self.config.max_workers,
        )
        self._shared_session = getattr(self.spot_ticker, "session", None)

        ticker_kwargs = {"timeout": self.config.timeout}
        if self._shared_session is not None:
            ticker_kwargs["session"] = self._shared_session

        self.options_tickers = {
            symbol: self._create_ticker(symbol, **ticker_kwargs)
            for symbol in self.config.options_symbols
        }
        self._spot_fallback_tickers: dict[str, Ticker] = {}
        self.skew_ticker = self._create_ticker(
            self.config.skew_symbol,
            **ticker_kwargs,
        )

        self._cache_file = self.config.output_dir / "state" / "monthly_expirations.json"
        self._load_expiration_cache()

    def run(self, cycles: Optional[int] = None) -> None:
        cycle_idx = 0
        while cycles is None or cycle_idx < cycles:
            cycle_started = time.monotonic()
            cycle_time_utc = self._now_fn(timezone.utc)

            metrics = self.run_cycle(cycle_time_utc)
            duration_seconds = round(time.monotonic() - cycle_started, 4)
            metrics["duration_seconds"] = duration_seconds
            self._append_metrics(cycle_time_utc.date(), metrics)

            logger.info(
                "cycle=%s duration=%.3fs spot=%s option_calls=%s option_contracts=%s "
                "skew=%s errors=%s",
                cycle_idx + 1,
                duration_seconds,
                metrics["spot_snapshots_written"],
                metrics["option_api_calls"],
                metrics["option_contracts"],
                metrics["skew_snapshots_written"],
                len(metrics["errors"]),
            )

            cycle_idx += 1
            if cycles is not None and cycle_idx >= cycles:
                break

            sleep_seconds = max(0.0, self.config.interval_seconds - duration_seconds)
            self._sleep_fn(sleep_seconds)

    def run_cycle(self, cycle_time_utc: Optional[datetime] = None) -> dict:
        cycle_time_utc = cycle_time_utc or self._now_fn(timezone.utc)
        cycle_slug = cycle_time_utc.strftime("%Y%m%dT%H%M%SZ")
        metrics = {
            "cycle_time_utc": cycle_time_utc.isoformat(),
            "spot_snapshots_written": 0,
            "option_api_calls": 0,
            "option_snapshots_written": 0,
            "option_contracts": 0,
            "skew_snapshots_written": 0,
            "errors": [],
        }

        spot_written, spot_errors = self._capture_spot_snapshots(
            cycle_time_utc, cycle_slug
        )
        metrics["spot_snapshots_written"] = spot_written
        metrics["errors"].extend(spot_errors)

        option_metrics = self._capture_option_snapshots(cycle_time_utc, cycle_slug)
        for key in ["option_api_calls", "option_snapshots_written", "option_contracts"]:
            metrics[key] = option_metrics[key]
        metrics["errors"].extend(option_metrics["errors"])

        skew_written, skew_errors = self._capture_skew_snapshot(cycle_time_utc, cycle_slug)
        metrics["skew_snapshots_written"] = skew_written
        metrics["errors"].extend(skew_errors)

        return metrics

    def _capture_spot_snapshots(
        self,
        cycle_time_utc: datetime,
        cycle_slug: str,
    ) -> tuple[int, list[str]]:
        quotes, error = self._request_with_retry(
            lambda: self.spot_ticker.quotes,
            action_name="spot_quotes",
        )
        if error:
            return 0, [error]
        quotes_map, normalize_errors = self._normalize_quotes_payload(quotes)
        if normalize_errors and not quotes_map:
            logger.warning("spot quote normalization warnings=%s", normalize_errors)

        snapshot_time = cycle_time_utc.isoformat()
        written = 0
        errors = [f"spot_quotes: {error_text}" for error_text in normalize_errors]
        for symbol in self.config.spot_symbols:
            payload = self._extract_symbol_payload(quotes_map, symbol)
            if not isinstance(payload, dict):
                payload, fallback_error = self._fetch_spot_fallback(symbol)
                if fallback_error:
                    errors.append(fallback_error)
                    continue
            if not isinstance(payload, dict):
                errors.append(f"spot_quotes:{symbol}: invalid quote payload")
                continue
            record = {"symbol": symbol, "snapshotTime": snapshot_time}
            record.update(payload)
            output_path = self._snapshot_path(
                cycle_time_utc.date(),
                dataset="spot",
                symbol=symbol,
                cycle_slug=cycle_slug,
                extension="json",
            )
            self._write_json(output_path, record)
            written += 1
        return written, errors

    def _fetch_spot_fallback(self, symbol: str) -> tuple[object, Optional[str]]:
        ticker = self._spot_fallback_tickers.get(symbol)
        if ticker is None:
            ticker_kwargs = {"timeout": self.config.timeout}
            if self._shared_session is not None:
                ticker_kwargs["session"] = self._shared_session
            try:
                ticker = self._create_ticker(symbol, **ticker_kwargs)
            except RuntimeError as exc:
                return None, f"spot_fallback:{symbol}: {exc}"
            self._spot_fallback_tickers[symbol] = ticker

        data, error = self._request_with_retry(
            lambda: ticker.price,
            action_name=f"spot_fallback:{symbol}",
        )
        if error:
            return None, error

        payload = self._extract_symbol_payload(data, symbol)
        if not isinstance(payload, dict):
            return None, f"spot_fallback:{symbol}: invalid fallback payload"
        return payload, None

    def _capture_option_snapshots(self, cycle_time_utc: datetime, cycle_slug: str) -> dict:
        self._ensure_expiration_cache(cycle_time_utc)

        metrics = {
            "option_api_calls": 0,
            "option_snapshots_written": 0,
            "option_contracts": 0,
            "errors": [],
        }
        snapshot_time = cycle_time_utc.isoformat()

        for symbol, ticker in self.options_tickers.items():
            expirations = self._expiration_cache.get(symbol, [])
            for expiration in expirations:
                metrics["option_api_calls"] += 1
                data, error = self._request_with_retry(
                    lambda exp=expiration: ticker._get_data("options", {"date": exp}),
                    action_name=f"options:{symbol}:{expiration}",
                )
                if error:
                    metrics["errors"].append(error)
                    continue

                symbol_payload = self._extract_symbol_payload(data, symbol)
                if not isinstance(symbol_payload, dict):
                    metrics["errors"].append(
                        f"options:{symbol}:{expiration}: missing symbol payload"
                    )
                    continue

                option_groups = symbol_payload.get("options") or []
                df = options_to_dataframe(option_groups, symbol)
                if df.empty:
                    continue

                df["snapshotTime"] = snapshot_time
                df["snapshotTimestamp"] = int(cycle_time_utc.timestamp())
                expiration_date = datetime.fromtimestamp(
                    expiration, tz=timezone.utc
                ).date()
                output_path = self._snapshot_path(
                    cycle_time_utc.date(),
                    dataset="options",
                    symbol=symbol,
                    cycle_slug=cycle_slug,
                    expiration=expiration_date.isoformat(),
                    extension="csv",
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(output_path, index=False)
                metrics["option_snapshots_written"] += 1
                metrics["option_contracts"] += len(df.index)

        return metrics

    def _capture_skew_snapshot(
        self,
        cycle_time_utc: datetime,
        cycle_slug: str,
    ) -> tuple[int, list[str]]:
        local_time = cycle_time_utc.astimezone(self._market_tz)
        due = should_capture_skew(
            local_time,
            self._skew_last_capture_date,
            self.config.skew_capture_hour,
            self.config.skew_capture_minute,
        )
        if not due:
            return 0, []

        quotes, error = self._request_with_retry(
            lambda: self.skew_ticker.quotes,
            action_name="skew_quote",
        )
        if error:
            return 0, [error]
        quotes_map, normalize_errors = self._normalize_quotes_payload(quotes)
        if normalize_errors and not quotes_map:
            return 0, [f"skew_quote: {normalize_errors[0]}"]
        if normalize_errors:
            logger.warning("skew quote normalization warnings=%s", normalize_errors)

        symbol = self.config.skew_symbol
        payload = quotes_map.get(symbol)
        if not isinstance(payload, dict):
            symbol_payload = self._extract_symbol_payload(quotes_map, symbol)
            if isinstance(symbol_payload, dict):
                payload = symbol_payload
            else:
                return 0, [f"skew_quote:{symbol}: missing symbol payload"]

        record = {"symbol": symbol, "snapshotTime": cycle_time_utc.isoformat()}
        record.update(payload)
        output_path = self._snapshot_path(
            cycle_time_utc.date(),
            dataset="daily_close",
            symbol=symbol,
            cycle_slug=cycle_slug,
            extension="json",
        )
        self._write_json(output_path, record)
        self._skew_last_capture_date = local_time.date()
        return 1, []

    def _ensure_expiration_cache(self, cycle_time_utc: datetime) -> None:
        if self._expiration_cache and self._expiration_cache_updated_at:
            cache_age = cycle_time_utc - self._expiration_cache_updated_at
            refresh_window = timedelta(hours=self.config.refresh_expirations_hours)
            if cache_age < refresh_window:
                return
        self._refresh_expiration_cache(cycle_time_utc)

    def _refresh_expiration_cache(self, cycle_time_utc: datetime) -> None:
        next_cache: dict[str, list[int]] = {}
        errors = []
        for symbol, ticker in self.options_tickers.items():
            data, error = self._request_with_retry(
                lambda: ticker._get_data("options"),
                action_name=f"expirations:{symbol}",
            )
            if error:
                errors.append(error)
                continue

            symbol_payload = self._extract_symbol_payload(data, symbol)
            if not isinstance(symbol_payload, dict):
                errors.append(f"expirations:{symbol}: missing symbol payload")
                continue

            expiration_dates = symbol_payload.get("expirationDates") or []
            monthly_expirations = select_monthly_expirations(
                expiration_dates,
                as_of_utc=cycle_time_utc,
                lookahead_months=self.config.lookahead_months,
            )
            next_cache[symbol] = monthly_expirations

        if errors:
            logger.warning("expiration refresh errors=%s", errors)

        if next_cache:
            self._expiration_cache = next_cache
            self._expiration_cache_updated_at = cycle_time_utc
            self._save_expiration_cache()

    def _load_expiration_cache(self) -> None:
        if not self._cache_file.exists():
            return
        try:
            payload = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        updated_at = payload.get("updated_at")
        symbols = payload.get("symbols")
        if not isinstance(symbols, dict):
            return

        cache: dict[str, list[int]] = {}
        for symbol, values in symbols.items():
            if not isinstance(values, list):
                continue
            cache[symbol] = [int(value) for value in values if isinstance(value, int)]

        if not cache:
            return

        self._expiration_cache = cache
        if isinstance(updated_at, str):
            try:
                parsed = datetime.fromisoformat(updated_at)
                self._expiration_cache_updated_at = (
                    parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                )
            except ValueError:
                self._expiration_cache_updated_at = None

    def _save_expiration_cache(self) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": (
                self._expiration_cache_updated_at.isoformat()
                if self._expiration_cache_updated_at
                else None
            ),
            "symbols": self._expiration_cache,
        }
        self._cache_file.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )

    def _snapshot_path(
        self,
        snapshot_date: date,
        dataset: str,
        symbol: str,
        cycle_slug: str,
        extension: str,
        expiration: Optional[str] = None,
    ) -> Path:
        path = (
            self.config.output_dir
            / snapshot_date.isoformat()
            / dataset
            / f"symbol={quote(symbol, safe='')}"
        )
        if expiration:
            path = path / f"expiration={expiration}"
        return path / f"snapshot_{cycle_slug}.{extension}"

    @staticmethod
    def _extract_symbol_payload(data: object, symbol: str) -> object:
        if not isinstance(data, dict):
            return None
        if symbol in data:
            return data[symbol]
        symbol_upper = symbol.upper()
        symbol_lower = symbol.lower()
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            if key.upper() == symbol_upper or key.lower() == symbol_lower:
                return value
        if len(data) == 1:
            return next(iter(data.values()))
        return None

    @staticmethod
    def _normalize_quotes_payload(payload: object) -> tuple[dict[str, dict], list[str]]:
        if isinstance(payload, dict):
            return payload, []
        if isinstance(payload, list):
            normalized = {}
            errors = []
            for entry in payload:
                if not isinstance(entry, dict):
                    errors.append("unexpected list entry in quotes payload")
                    continue
                symbol = entry.get("symbol")
                if not symbol:
                    errors.append("missing symbol field in quotes payload")
                    continue
                symbol_key = str(symbol)
                normalized[symbol_key] = {k: v for k, v in entry.items() if k != "symbol"}
            return normalized, errors
        return {}, ["unexpected quotes payload type"]

    def _request_with_retry(
        self,
        action: Callable[[], object],
        action_name: str,
    ) -> tuple[object, Optional[str]]:
        attempts = self.config.retries + 1
        last_error = "Unknown error"

        for attempt in range(1, attempts + 1):
            try:
                payload = action()
            except Exception as exc:  # pragma: no cover - defensive path
                message = str(exc).strip()
                last_error = message if message else exc.__class__.__name__
            else:
                if isinstance(payload, dict) and payload.get("error"):
                    last_error = str(payload["error"])
                else:
                    return payload, None

            if attempt < attempts:
                backoff = self.config.backoff_seconds * attempt
                self._sleep_fn(backoff)

        return None, f"{action_name}: {last_error}"

    def _create_ticker(self, symbols, **kwargs) -> Ticker:
        attempts = self.config.retries + 1
        last_error = "unknown ticker initialization error"

        for attempt in range(1, attempts + 1):
            try:
                return self._ticker_factory(symbols, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive path
                message = str(exc).strip()
                last_error = message if message else exc.__class__.__name__
            if attempt < attempts:
                backoff = self.config.backoff_seconds * attempt
                self._sleep_fn(backoff)

        raise RuntimeError(f"ticker_init:{symbols}: {last_error}")

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, default=str, sort_keys=True), encoding="utf-8")

    def _append_metrics(self, snapshot_date: date, metrics: dict) -> None:
        output_path = (
            self.config.output_dir / snapshot_date.isoformat() / "metrics" / "cycle_metrics.csv"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "cycle_time_utc",
            "spot_snapshots_written",
            "option_api_calls",
            "option_snapshots_written",
            "option_contracts",
            "skew_snapshots_written",
            "duration_seconds",
            "errors",
        ]
        row = dict(metrics)
        row["errors"] = "; ".join(metrics["errors"])
        has_header = output_path.exists()
        with output_path.open("a", newline="", encoding="utf-8") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fields)
            if not has_header:
                writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in fields})


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect 1m Yahoo snapshots")
    parser.add_argument(
        "--output-dir",
        default="data/raw_snapshots",
        help="Directory for partitioned snapshot files.",
    )
    parser.add_argument(
        "--spot-symbols",
        default=",".join(DEFAULT_SPOT_SYMBOLS),
        help="Comma-separated symbols for 1m spot snapshots.",
    )
    parser.add_argument(
        "--options-symbols",
        default=",".join(DEFAULT_OPTIONS_SYMBOLS),
        help="Comma-separated symbols for options snapshot collection.",
    )
    parser.add_argument(
        "--skew-symbol",
        default=DEFAULT_SKEW_SYMBOL,
        help="Symbol collected once a day after market close.",
    )
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--cycles", type=int, default=None)
    parser.add_argument("--lookahead-months", type=int, default=12)
    parser.add_argument("--refresh-expirations-hours", type=int, default=24)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--backoff-seconds", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--market-timezone", default="America/New_York")
    parser.add_argument("--skew-capture-hour", type=int, default=16)
    parser.add_argument("--skew-capture-minute", type=int, default=5)
    parser.add_argument("--log-level", default="INFO")
    return parser


def build_config_from_args(args: argparse.Namespace) -> CollectorConfig:
    return CollectorConfig(
        output_dir=Path(args.output_dir),
        interval_seconds=args.interval_seconds,
        lookahead_months=args.lookahead_months,
        refresh_expirations_hours=args.refresh_expirations_hours,
        retries=args.retries,
        backoff_seconds=args.backoff_seconds,
        timeout=args.timeout,
        max_workers=args.max_workers,
        market_timezone=args.market_timezone,
        skew_capture_hour=args.skew_capture_hour,
        skew_capture_minute=args.skew_capture_minute,
        spot_symbols=parse_symbol_list(args.spot_symbols),
        options_symbols=parse_symbol_list(args.options_symbols),
        skew_symbol=args.skew_symbol,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    config = build_config_from_args(args)
    collector = SnapshotCollector(config)
    collector.run(cycles=args.cycles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

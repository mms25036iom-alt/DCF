"""Company-specific historical price-return beta against India's Nifty 50.

Yahoo ``Close`` prices are requested with auto_adjust=False. Their corporate-
action treatment is not independently certified; this is a disclosed historical
price-return estimate, not a verified forward-looking risk parameter.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

BENCHMARK = "^NSEI"
_CACHE_VERSION = 1
_MIN_RETURNS = 78
_METHOD = (
    "OLS of two-year weekly simple Close price returns against Nifty 50 (^NSEI); "
    "last common trading-date close in each completed Friday-ending week; "
    "consecutive weeks only, no forward filling; auto_adjust=False, repair=False"
)


def _today() -> date:
    return datetime.now(UTC).date()


def _finite(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _start(today: date) -> date:
    try:
        return today.replace(year=today.year - 2)
    except ValueError:
        return today.replace(year=today.year - 2, day=28)


def _dated(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _close_prices(frame, today: date) -> dict[date, float]:
    """Read bounded daily Close observations without rescaling or filling gaps."""
    if frame is None or len(frame) > 2000:
        raise ValueError("Daily price history is absent or exceeds its bounds")
    series = frame["Close"]
    if not hasattr(series, "items"):
        raise ValueError("Daily Close prices are unavailable")
    prices = {}
    for stamp, raw in series.items():
        observed, value = _dated(stamp), _finite(raw)
        if observed is None or not _start(today) <= observed < today:
            continue
        if observed.weekday() > 4 or value is None or value <= 0:
            continue
        if observed in prices:
            raise ValueError("Daily Close dates are duplicated")
        prices[observed] = value
    return prices


def calculate_weekly_beta(stock_closes: Mapping, benchmark_closes: Mapping, today: date) -> dict:
    """Pure OLS helper on daily date/Close mappings; no network or assumptions.

    Weekly endpoints use the same actual date for both series. A missing week
    removes both adjoining returns, rather than turning a multiweek change into
    a weekly return. The current week is excluded even on Friday to avoid using
    an intraday last price as a completed weekly close.
    """
    if len(stock_closes) > 2000 or len(benchmark_closes) > 2000:
        raise ValueError("Daily price history exceeds its bounds")
    endpoints = {}
    for observed in sorted(set(stock_closes) & set(benchmark_closes)):
        if not isinstance(observed, date) or isinstance(observed, datetime):
            raise ValueError("Daily prices require calendar dates")
        if not _start(today) <= observed < today or observed.weekday() > 4:
            continue
        week = observed - timedelta(days=observed.weekday())
        if week + timedelta(days=4) >= today:
            continue
        stock, benchmark = _finite(stock_closes[observed]), _finite(benchmark_closes[observed])
        if stock is not None and benchmark is not None and stock > 0 and benchmark > 0:
            endpoints[week] = (observed, stock, benchmark)
    weeks = sorted(endpoints)
    paired = []
    for previous, current in zip(weeks, weeks[1:], strict=False):
        if current - previous != timedelta(days=7):
            continue
        prior, closing = endpoints[previous], endpoints[current]
        stock_return = closing[1] / prior[1] - 1
        market_return = closing[2] / prior[2] - 1
        if math.isfinite(stock_return) and math.isfinite(market_return):
            paired.append((closing[0], stock_return, market_return))
    count = len(paired)
    if count < _MIN_RETURNS:
        raise ValueError("At least 78 paired weekly returns are required")
    latest = paired[-1][0]
    if not 0 <= (today - latest).days <= 14:
        raise ValueError("The latest paired weekly price observation is stale")
    average_stock = math.fsum(row[1] for row in paired) / count
    average_market = math.fsum(row[2] for row in paired) / count
    market_squares = math.fsum((row[2] - average_market) ** 2 for row in paired)
    stock_squares = math.fsum((row[1] - average_stock) ** 2 for row in paired)
    covariance_sum = math.fsum(
        (row[1] - average_stock) * (row[2] - average_market) for row in paired
    )
    if market_squares <= 0 or stock_squares <= 0:
        raise ValueError("Return variance must be positive")
    beta = covariance_sum / market_squares
    intercept = average_stock - beta * average_market
    residual_squares = math.fsum((row[1] - intercept - beta * row[2]) ** 2 for row in paired)
    r_squared = covariance_sum**2 / (market_squares * stock_squares)
    standard_error = math.sqrt(residual_squares / (count - 2) / market_squares)
    if not all(math.isfinite(value) for value in (beta, intercept, r_squared, standard_error)):
        raise ValueError("The estimated beta statistics are not finite")
    # Floating-point roundoff can put a perfect fit a few ulps above one.
    r_squared = max(0.0, min(1.0, r_squared))
    return {
        "value": beta,
        "as_of": latest.isoformat(),
        "observations": count,
        "r_squared": r_squared,
        "standard_error": standard_error,
        "intercept": intercept,
        "window_start": _start(today).isoformat(),
        "first_return_date": paired[0][0].isoformat(),
        "last_return_date": latest.isoformat(),
    }


def _empty(symbol: str, warning: str) -> dict:
    return {
        "value": None,
        "as_of": None,
        "benchmark": BENCHMARK,
        "method": _METHOD,
        "observations": 0,
        "r_squared": None,
        "standard_error": None,
        "warnings": [warning],
        "source_url": "https://finance.yahoo.com/quote/"
        + quote(str(symbol), safe="")
        + "/history/",
        "benchmark_source_url": "https://finance.yahoo.com/quote/%5ENSEI/history/",
        "cache_hit": False,
    }


def _cache_path(settings, symbol: str) -> Path:
    root = Path(getattr(settings, "artifact_storage_root", ".private/artifacts"))
    return root / "cache" / "beta" / (sha256(symbol.encode("utf-8")).hexdigest() + ".json")


def _read_cache(settings, symbol: str, today: date) -> dict | None:
    try:
        path = _cache_path(settings, symbol)
        if path.stat().st_size > 65536:
            return None
        result = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now(UTC) - datetime.fromisoformat(result["retrieved_at"])).total_seconds()
        as_of = date.fromisoformat(result["as_of"])
        if (
            result.get("cache_version") != _CACHE_VERSION
            or result.get("symbol") != symbol
            or result.get("benchmark") != BENCHMARK
            or result.get("method") != _METHOD
            or not 0 <= age <= 7 * 86400
            or not 0 <= (today - as_of).days <= 14
            or not isinstance(result.get("observations"), int)
            or result["observations"] < _MIN_RETURNS
            or any(
                _finite(result.get(key)) is None for key in ("value", "r_squared", "standard_error")
            )
            or not 0 <= result["r_squared"] <= 1
            or result["standard_error"] < 0
        ):
            return None
        result["cache_hit"] = True
        return copy.deepcopy(result)
    except (OSError, TypeError, ValueError, KeyError):
        return None


def _write_cache(settings, symbol: str, result: dict) -> None:
    temporary = None
    try:
        path = _cache_path(settings, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def estimate_india_beta(company, yf_module, settings, symbol: str, refresh: bool = False) -> dict:
    """Estimate beta for a caller-verified INR .NS/.BO listing, or leave it missing.

    No company information or credentials are requested. A provider fake without
    history support returns immediately before constructing a benchmark ticker.
    Each of the two price-history calls receives a ten-second network timeout.
    """
    if not isinstance(symbol, str) or not re.fullmatch(
        r"[A-Z0-9][A-Z0-9&.\-]{0,39}\.(?:NS|BO)", symbol
    ):
        return _empty(
            symbol, "India beta estimation requires an explicit verified .NS or .BO listing."
        )
    if not callable(getattr(company, "history", None)):
        return _empty(
            symbol, "Company price history is unavailable; no benchmark series was requested."
        )
    today = _today()
    if not refresh:
        cached = _read_cache(settings, symbol, today)
        if cached is not None:
            return cached
    result = _empty(symbol, "A usable two-year weekly beta estimate could not be established.")
    try:
        arguments = {
            "start": _start(today).isoformat(),
            "end": today.isoformat(),
            "interval": "1d",
            "auto_adjust": False,
            "back_adjust": False,
            "repair": False,
            "actions": False,
            "timeout": 10,
        }
        stock_prices = _close_prices(company.history(**arguments), today)
        if len(stock_prices) < _MIN_RETURNS + 1:
            return _empty(symbol, "Insufficient company daily price history; beta remains missing.")
        benchmark = yf_module.Ticker(BENCHMARK)
        benchmark_prices = _close_prices(benchmark.history(**arguments), today)
        statistics = calculate_weekly_beta(stock_prices, benchmark_prices, today)
        result.update(statistics)
        result.update(
            {
                "symbol": symbol,
                "cache_version": _CACHE_VERSION,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "price_basis": "Yahoo daily Close with auto_adjust=False; weekly price returns, not total returns",
                "documentation_url": "https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html",
                "warnings": [
                    "Historical company beta is estimated from Yahoo Close prices against Nifty 50; "
                    "corporate-action adjustment quality is not independently verified and the estimate is not forward-looking.",
                    "OLS standard error assumes independent, constant-variance residuals; it does not measure all model uncertainty.",
                ],
            }
        )
        _write_cache(settings, symbol, result)
        return result
    except Exception:
        return result

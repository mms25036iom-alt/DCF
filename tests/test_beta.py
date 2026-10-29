"""Offline beta fixtures exercise statistical and calendar alignment contracts."""

import json
import math
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from research.providers import beta

TODAY = date(2026, 10, 29)


def daily_prices(slope=1.7, intercept=0.001, noise=0.0, weeks=104):
    last_friday = TODAY - timedelta(days=(TODAY.weekday() - 4) % 7)
    first = last_friday - timedelta(weeks=weeks)
    stock, market = {}, {}
    stock_price, market_price = 100.0, 1000.0
    for step in range(weeks + 1):
        observed = first + timedelta(weeks=step)
        if step:
            market_return = 0.02 * math.sin(step * 0.7) + 0.006 * math.cos(step * 0.13)
            stock_return = intercept + slope * market_return + noise * math.cos(step * 1.1)
            stock_price *= 1 + stock_return
            market_price *= 1 + market_return
        stock[observed], market[observed] = stock_price, market_price
    return stock, market


def frame(prices):
    return pd.DataFrame({"Close": list(prices.values())}, index=pd.to_datetime(list(prices)))


class FakeTicker:
    def __init__(self, prices):
        self.prices, self.calls = prices, []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return frame(self.prices)


@pytest.fixture
def provider_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(beta, "_today", lambda: TODAY)
    stock, market = daily_prices(noise=0.003)
    company, benchmark = FakeTicker(stock), FakeTicker(market)
    symbols = []

    def ticker(symbol):
        symbols.append(symbol)
        return benchmark

    return company, benchmark, SimpleNamespace(Ticker=ticker), SimpleNamespace(
        artifact_storage_root=tmp_path
    ), symbols


def test_ols_recovers_known_slope_intercept_and_fit_without_cap():
    stock, market = daily_prices(slope=6.4)
    result = beta.calculate_weekly_beta(stock, market, TODAY)
    assert result["value"] == pytest.approx(6.4)
    assert result["intercept"] == pytest.approx(0.001)
    assert result["r_squared"] == pytest.approx(1.0)
    assert result["standard_error"] == pytest.approx(0.0, abs=1e-12)
    assert result["observations"] >= 100
    assert result["as_of"] == "2026-10-23"


def test_negative_beta_is_preserved_as_a_statistical_observation():
    stock, market = daily_prices(slope=-0.4)
    assert beta.calculate_weekly_beta(stock, market, TODAY)["value"] == pytest.approx(-0.4)


def test_standard_error_and_r_squared_match_explicit_ols_formula():
    stock, market = daily_prices(noise=0.01)
    result = beta.calculate_weekly_beta(stock, market, TODAY)
    dates = sorted(stock)
    pairs = [(stock[current] / stock[previous] - 1, market[current] / market[previous] - 1)
             for previous, current in zip(dates, dates[1:], strict=False)]
    # The first endpoint can precede the exact two-year window.
    pairs = [(stock[current] / stock[previous] - 1, market[current] / market[previous] - 1)
             for previous, current in zip(dates, dates[1:], strict=False)
             if previous >= date(2024, 10, 29)]
    mean_y = sum(item[0] for item in pairs) / len(pairs)
    mean_x = sum(item[1] for item in pairs) / len(pairs)
    sxx = sum((item[1] - mean_x) ** 2 for item in pairs)
    sxy = sum((item[0] - mean_y) * (item[1] - mean_x) for item in pairs)
    expected_slope = sxy / sxx
    intercept = mean_y - expected_slope * mean_x
    residual = sum((item[0] - intercept - expected_slope * item[1]) ** 2 for item in pairs)
    expected_error = math.sqrt(residual / (len(pairs) - 2) / sxx)
    assert result["value"] == pytest.approx(expected_slope)
    assert result["standard_error"] == pytest.approx(expected_error)
    assert 0 < result["r_squared"] < 1


def test_weekly_endpoints_use_same_actual_trading_date_and_skip_gaps():
    stock, market = daily_prices()
    baseline = beta.calculate_weekly_beta(stock, market, TODAY)
    removed_week = sorted(stock)[40]
    del stock[removed_week]
    result = beta.calculate_weekly_beta(stock, market, TODAY)
    assert result["observations"] == baseline["observations"] - 2
    assert result["value"] == pytest.approx(1.7)
    # Friday absent for one listing: both use Thursday, never Thursday vs Friday.
    another_friday = sorted(stock)[60]
    thursday = another_friday - timedelta(days=1)
    stock[thursday] = stock.pop(another_friday)
    market[thursday] = market[another_friday]
    aligned = beta.calculate_weekly_beta(stock, market, TODAY)
    assert aligned["value"] == pytest.approx(1.7)


def test_current_week_and_future_prices_are_excluded():
    stock, market = daily_prices()
    baseline = beta.calculate_weekly_beta(stock, market, TODAY)
    for observed in (date(2026, 10, 27), date(2026, 10, 30), date(2027, 1, 1)):
        stock[observed], market[observed] = 99999, 1
    assert beta.calculate_weekly_beta(stock, market, TODAY) == baseline


@pytest.mark.parametrize("failure", ["insufficient", "constant_market", "constant_stock", "stale"])
def test_unusable_series_does_not_produce_a_beta(failure):
    stock, market = daily_prices(weeks=50 if failure == "insufficient" else 104)
    if failure == "constant_market":
        market = dict.fromkeys(market, 1000)
    elif failure == "constant_stock":
        stock = dict.fromkeys(stock, 100)
    elif failure == "stale":
        stock = {stamp: value for stamp, value in stock.items() if stamp < TODAY - timedelta(days=30)}
    with pytest.raises(ValueError):
        beta.calculate_weekly_beta(stock, market, TODAY)


def test_provider_uses_two_bounded_history_calls_and_discloses_method(provider_fixture):
    company, benchmark, yf, settings, symbols = provider_fixture
    result = beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")
    assert result["value"] is not None
    assert result["benchmark"] == "^NSEI"
    assert symbols == ["^NSEI"]
    assert len(company.calls) == len(benchmark.calls) == 1
    assert company.calls[0] == benchmark.calls[0]
    assert company.calls[0]["timeout"] == 10
    assert company.calls[0]["interval"] == "1d"
    assert company.calls[0]["start"] == "2024-10-29"
    assert company.calls[0]["end"] == "2026-10-29"
    assert company.calls[0]["auto_adjust"] is False
    assert "price returns, not total returns" in result["price_basis"]
    assert any("corporate-action" in item for item in result["warnings"])


def test_history_absent_or_wrong_listing_returns_before_benchmark_network(tmp_path):
    def unexpected(symbol):
        pytest.fail("Benchmark ticker must not be constructed")

    yf = SimpleNamespace(Ticker=unexpected)
    settings = SimpleNamespace(artifact_storage_root=tmp_path)
    assert beta.estimate_india_beta(object(), yf, settings, "RELIANCE.NS")["value"] is None
    assert beta.estimate_india_beta(object(), yf, settings, "AAPL")["value"] is None


def test_cache_is_listing_specific_and_refresh_does_not_return_old_beta(provider_fixture):
    company, benchmark, yf, settings, symbols = provider_fixture
    first = beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")
    assert beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")["cache_hit"] is True
    assert len(company.calls) == len(benchmark.calls) == 1
    beta.estimate_india_beta(company, yf, settings, "TCS.NS")
    assert symbols == ["^NSEI", "^NSEI"]
    company.prices = {}
    failed = beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS", refresh=True)
    assert failed["value"] is None
    assert failed["as_of"] is None
    assert first["value"] is not None


def test_expired_or_future_cache_is_not_used(provider_fixture):
    company, _, yf, settings, _ = provider_fixture
    beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")
    path = beta._cache_path(settings, "RELIANCE.NS")
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["retrieved_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    path.write_text(json.dumps(cached), encoding="utf-8")
    assert beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")["cache_hit"] is False
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["as_of"] = "2026-10-30"
    path.write_text(json.dumps(cached), encoding="utf-8")
    assert beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")["cache_hit"] is False


def test_provider_failures_are_missing_and_sanitized(provider_fixture):
    company, _, yf, settings, _ = provider_fixture

    def unavailable(**kwargs):
        raise RuntimeError("Authorization secret=do-not-copy")

    company.history = unavailable
    result = beta.estimate_india_beta(company, yf, settings, "RELIANCE.NS")
    assert result["value"] is None
    assert "do-not-copy" not in json.dumps(result)


def test_invalid_prices_and_duplicate_dates_are_not_filled():
    stock, _ = daily_prices()
    source = frame(stock)
    source.iloc[10, 0], source.iloc[20, 0], source.iloc[30, 0] = float("nan"), -1, float("inf")
    cleaned = beta._close_prices(source, TODAY)
    assert len(cleaned) == len(source[source.index.date >= date(2024, 10, 29)]) - 3
    with pytest.raises(ValueError, match="duplicated"):
        beta._close_prices(pd.concat([source, source.tail(1)]), TODAY)

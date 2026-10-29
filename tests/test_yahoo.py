"""Yahoo fixtures exercise provider contracts without contacting Yahoo."""

import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from research.providers.base import ProviderError
from research.providers.yahoo import (
    YahooFinanceProvider,
    YahooInvalidDataError,
    YahooRateLimitError,
    YahooUnavailableError,
)


class FakeTicker:
    def __init__(self):
        self.info = {
            "symbol": "RELIANCE.NS",
            "longName": "Reliance Industries Limited",
            "sector": "Energy",
            "exchange": "NSI",
            "currency": "INR",
            "financialCurrency": "INR",
            "regularMarketPrice": 1500,
            "regularMarketTime": 1792900800,
            "marketCap": 20_000_000_000_000,
            "sharesOutstanding": 13_000_000_000,
            "website": "https://www.ril.com",
        }
        self.income = {
            datetime(2025, 3, 31, tzinfo=UTC): {
                "TotalRevenue": 1_000_000,
                "EBIT": 100_000,
                "NetIncome": 70_000,
                "EBITDA": 130_000,
                "TaxProvision": 20_000,
                "PretaxIncome": 90_000,
            },
            "2024-03-31": {"Total Revenue": 900_000, "EBIT": 80_000, "NetIncome": 60_000},
        }
        self.balance = {
            "2025-03-31": {
                "TotalDebt": 200_000,
                "CashAndCashEquivalents": 100_000,
                "StockholdersEquity": 500_000,
            }
        }
        self.cashflow = {
            "2025-03-31": {
                "OperatingCashFlow": 110_000,
                "CapitalExpenditure": -40_000,
                "DepreciationDepletionAndAmortization": 30_000,
                "ChangeInWorkingCapital": -10_000,
            },
        }
        self.calls = []
        self.news = []

    def get_info(self):
        self.calls.append("info")
        return self.info

    def get_income_stmt(self, **kwargs):
        self.calls.append(("income", kwargs))
        return self.income

    def get_balance_sheet(self, **kwargs):
        self.calls.append(("balance", kwargs))
        return self.balance

    def get_cashflow(self, **kwargs):
        self.calls.append(("cashflow", kwargs))
        return self.cashflow

    def get_news(self, **kwargs):
        self.calls.append(("news", kwargs))
        return self.news


@pytest.fixture
def yahoo_fixture(monkeypatch, tmp_path):
    ticker = FakeTicker()
    symbols = []

    def factory(symbol):
        symbols.append(symbol)
        return ticker

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=factory))
    settings = SimpleNamespace(
        artifact_storage_root=tmp_path,
        yahoo_cache_ttl_seconds=3600,
        yahoo_max_retries=0,
    )
    return YahooFinanceProvider(settings), ticker, symbols, settings


def test_yahoo_normalizes_annual_periods_and_retains_absolute_currency_units(yahoo_fixture):
    provider, ticker, symbols, _ = yahoo_fixture
    result = provider.fetch_company(" reliance.ns ")
    assert symbols == ["RELIANCE.NS"]
    assert result["name"] == "Reliance Industries Limited"
    assert result["currency"] == result["financial_currency"] == "INR"
    assert result["fiscal_period_ends"] == ["2024-03-31", "2025-03-31"]
    history = result["financial_history"]
    assert history["years"] == [2024, 2025]
    assert history["revenue"] == [900_000, 1_000_000]
    assert history["ebit"] == [80_000, 100_000]
    assert history["operating_cash_flow"] == [None, 110_000]
    assert history["total_debt"] == [None, 200_000]
    assert history["capital_expenditure"] == [None, 40_000]
    assert history["depreciation"] == [None, 30_000]
    assert history["working_capital_cash_flow"] == [None, -10_000]
    assert history["income_tax"] == [None, 20_000]
    assert history["pretax_income"] == [None, 90_000]
    assert all(call[1]["freq"] == "yearly" for call in ticker.calls if isinstance(call, tuple))
    assert (
        result["price_as_of"]
        == datetime.fromtimestamp(ticker.info["regularMarketTime"], UTC).isoformat()
    )
    assert all(citation["provider"] == "yahoo-finance" for citation in result["citations"])
    assert not any("governance" in key or "evidence_score" in key for key in result)
    json.dumps(result, allow_nan=False)


def test_bare_indian_ticker_explicitly_records_nse_assumption(yahoo_fixture):
    provider, _, symbols, _ = yahoo_fixture
    result = provider.fetch_company("RELIANCE")
    assert symbols == ["RELIANCE.NS"]
    assert result["ticker"] == "RELIANCE.NS"
    assert any("interpreted as NSE" in warning for warning in result["warnings"])


def test_operating_income_is_preferred_to_vendor_ebit_and_basis_is_retained(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    for period in ticker.income.values():
        period["OperatingIncome"] = 60_000
    result = provider.fetch_company("RELIANCE.NS")
    assert result["financial_history"]["ebit"] == [60_000, 60_000]
    assert result["metric_sources"]["ebit"] == ["OperatingIncome", "OperatingIncome"]
    assert not any("Vendor EBIT is a proxy" in warning for warning in result["warnings"])


def test_vendor_ebit_fallback_is_disclosed_as_a_proxy(yahoo_fixture):
    provider, _, _, _ = yahoo_fixture
    result = provider.fetch_company("RELIANCE.NS")
    assert result["metric_sources"]["ebit"] == ["EBIT", "EBIT"]
    assert any("non-operating income" in warning for warning in result["warnings"])


@pytest.mark.parametrize("symbol", ["", "RELIANCE.NS,TCS.NS", "../secret", "A B", None])
def test_invalid_symbols_do_not_trigger_network_or_substitution(yahoo_fixture, symbol):
    provider, _, symbols, _ = yahoo_fixture
    with pytest.raises(YahooInvalidDataError):
        provider.fetch_company(symbol)
    assert symbols == []


def test_different_company_response_is_rejected(yahoo_fixture):
    provider, ticker, symbols, _ = yahoo_fixture
    ticker.info["symbol"] = "TCS.NS"
    with pytest.raises(YahooInvalidDataError, match="No other company was substituted"):
        provider.fetch_company("RELIANCE.NS")
    assert symbols == ["RELIANCE.NS"]


def test_missing_and_nonfinite_financial_values_stay_null(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    ticker.income = {
        "2025-03-31": {
            "TotalRevenue": float("nan"),
            "EBIT": float("inf"),
            "NetIncome": None,
        }
    }
    ticker.balance = {}
    ticker.cashflow = {}
    result = provider.fetch_company("RELIANCE.NS")
    history = result["financial_history"]
    assert history["revenue"] == history["ebit"] == history["total_debt"] == [None]
    assert any("Unavailable annual metrics" in warning for warning in result["warnings"])
    json.dumps(result, allow_nan=False)


def test_missing_statement_currency_withholds_amounts_without_assuming_inr(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    del ticker.info["financialCurrency"]
    result = provider.fetch_company("RELIANCE.NS")
    assert result["current_price"] == 1500
    assert result["financial_currency"] is None
    assert result["financial_history"]["revenue"] == [None, None]
    assert any("amounts are withheld" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "quote_currency,financial_currency",
    [
        ("INR", "USD"),
        (None, "INR"),
        ("GBp", "GBP"),
    ],
)
def test_unverified_or_mixed_currency_cannot_enter_valuation(
    yahoo_fixture, quote_currency, financial_currency
):
    provider, ticker, _, _ = yahoo_fixture
    ticker.info.update(currency=quote_currency, financialCurrency=financial_currency)
    with pytest.raises(YahooInvalidDataError):
        provider.fetch_company("RELIANCE.NS")


def test_missing_price_is_not_filled_from_retrieval_time_or_zero(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    ticker.info.pop("regularMarketPrice")
    ticker.info["currentPrice"] = 0
    result = provider.fetch_company("RELIANCE.NS")
    assert result["current_price"] is None
    assert result["price_as_of"] is None
    ticker.income = {}
    with pytest.raises(YahooInvalidDataError, match="neither a usable market price"):
        provider.fetch_company("RELIANCE.NS", refresh=True)


def test_absent_price_timestamp_stays_unknown(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    ticker.info.pop("regularMarketTime")
    result = provider.fetch_company("RELIANCE.NS")
    assert result["price_as_of"] is None
    assert any("as-of time is unknown" in warning for warning in result["warnings"])


def test_disk_cache_reuses_original_provenance_and_refresh_reloads(yahoo_fixture):
    provider, ticker, symbols, settings = yahoo_fixture
    first = provider.fetch_company("RELIANCE.NS")
    ticker.info["regularMarketPrice"] = 1600
    cached = YahooFinanceProvider(settings).fetch_company("RELIANCE.NS")
    assert len(symbols) == 1
    assert cached["cache_hit"]
    assert cached["current_price"] == first["current_price"] == 1500
    assert cached["retrieved_at"] == first["retrieved_at"]
    assert cached["price_as_of"] == first["price_as_of"]
    refreshed = provider.fetch_company("RELIANCE.NS", refresh=True)
    assert len(symbols) == 2
    assert refreshed["current_price"] == 1600
    assert not refreshed["cache_hit"]


def test_expired_cache_does_not_silently_mask_an_outage(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    provider.fetch_company("RELIANCE.NS")
    path = provider._cache_path("RELIANCE.NS")
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["cached_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(saved), encoding="utf-8")

    def fail():
        raise ConnectionError("network unavailable")

    ticker.get_info = fail
    with pytest.raises(YahooUnavailableError):
        provider.fetch_company("RELIANCE.NS")


@pytest.mark.parametrize("corruption", ["wrong_company", "misaligned_periods", "nonfinite"])
def test_structurally_invalid_cache_is_refetched(yahoo_fixture, corruption):
    provider, ticker, symbols, _ = yahoo_fixture
    provider.fetch_company("RELIANCE.NS")
    path = provider._cache_path("RELIANCE.NS")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "wrong_company":
        payload["data"]["ticker"] = "TCS.NS"
    elif corruption == "misaligned_periods":
        payload["data"]["financial_history"]["revenue"] = [100]
    else:
        payload["data"]["financial_history"]["revenue"][0] = float("nan")
    path.write_text(json.dumps(payload), encoding="utf-8")
    ticker.info["regularMarketPrice"] = 1700
    result = provider.fetch_company("RELIANCE.NS")
    assert len(symbols) == 2
    assert result["current_price"] == 1700


def test_corrupt_cache_is_ignored_and_local_cache_failure_is_nonfatal(yahoo_fixture):
    provider, _, symbols, settings = yahoo_fixture
    provider.cache_root.mkdir(parents=True)
    provider._cache_path("RELIANCE.NS").write_text("broken json", encoding="utf-8")
    assert provider.fetch_company("RELIANCE.NS")["current_price"] == 1500
    assert len(symbols) == 1
    blocked = settings.artifact_storage_root / "blocked"
    blocked.write_text("file", encoding="utf-8")
    provider.cache_root = blocked / "cache"
    result = provider.fetch_company("RELIANCE.NS", refresh=True)
    assert result["current_price"] == 1500
    assert any("could not be cached" in warning for warning in result["warnings"])


def test_rate_limit_is_typed_and_retries_are_bounded(yahoo_fixture, monkeypatch):
    provider, ticker, _, _ = yahoo_fixture
    provider.max_retries = 2
    attempts, pauses = [], []

    def fail():
        attempts.append(True)
        raise type("YFRateLimitError", (RuntimeError,), {})("Too Many Requests")

    ticker.get_info = fail
    monkeypatch.setattr("research.providers.yahoo.time.sleep", pauses.append)
    with pytest.raises(YahooRateLimitError, match="retry later") as caught:
        provider.fetch_company("RELIANCE.NS")
    assert isinstance(caught.value, ProviderError)
    assert len(attempts) == 3
    assert pauses == [0.5, 1.0]


def test_missing_statement_request_remains_missing_and_records_warning(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture

    def fail(**kwargs):
        raise ConnectionError("network unavailable")

    ticker.get_balance_sheet = fail
    result = provider.fetch_company("RELIANCE.NS")
    assert result["financial_history"]["total_debt"] == [None, None]
    assert any("annual balance statement" in warning for warning in result["warnings"])


def test_optional_news_normalizes_publication_timestamp(yahoo_fixture):
    provider, ticker, _, _ = yahoo_fixture
    provider.include_news = True
    ticker.news = [
        {
            "content": {
                "title": "Company update",
                "pubDate": "2026-10-25T10:00:00+05:30",
                "canonicalUrl": {"url": "https://example.com/report"},
            }
        }
    ]
    result = provider.fetch_company("RELIANCE.NS")
    assert result["news"] == [
        {
            "title": "Company update",
            "uri": "https://example.com/report",
            "published_at": "2026-10-25T04:30:00+00:00",
        }
    ]

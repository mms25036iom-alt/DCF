"""Keyless Yahoo Finance observations, with explicit identity/currency checks.

Yahoo is a secondary source. Financial values use absolute reported currency
units, never crore/lakh scaling. Missing observations remain null. No company
identity, governance score, or missing financial value is synthesized.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from research.providers.base import ProviderError
from research.providers.beta import estimate_india_beta

_CACHE_VERSION = 4
_METRICS = {
    "revenue": ("income", ("TotalRevenue", "OperatingRevenue")),
    "ebit": ("income", ("OperatingIncome", "OperatingIncomeLoss", "EBIT")),
    "net_income": ("income", ("NetIncome", "NetIncomeCommonStockholders")),
    "ebitda": ("income", ("EBITDA",)),
    "income_tax": ("income", ("TaxProvision",)),
    "pretax_income": ("income", ("PretaxIncome",)),
    "operating_cash_flow": ("cashflow", ("OperatingCashFlow", "TotalCashFromOperatingActivities")),
    "capital_expenditure": ("cashflow", ("CapitalExpenditure", "CapitalExpenditures")),
    "depreciation": (
        "cashflow",
        ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    ),
    "working_capital_cash_flow": ("cashflow", ("ChangeInWorkingCapital",)),
    "total_debt": ("balance", ("TotalDebt",)),
    "cash_and_equivalents": ("balance", ("CashAndCashEquivalents",)),
    "shareholders_equity": ("balance", ("StockholdersEquity", "TotalStockholderEquity")),
    "accounts_receivable": ("balance", ("AccountsReceivable", "NetReceivables")),
    "inventory": ("balance", ("Inventory",)),
    "accounts_payable": ("balance", ("AccountsPayable",)),
    "cost_of_revenue": ("income", ("CostOfRevenue",)),
    "current_assets": ("balance", ("CurrentAssets", "TotalCurrentAssets")),
    "current_liabilities": ("balance", ("CurrentLiabilities", "TotalCurrentLiabilities")),
    "interest_expense": ("income", ("InterestExpense", "InterestExpenseNonOperating")),
    "noncontrolling_interest": ("balance", ("MinorityInterest",)),
}


class YahooRateLimitError(ProviderError):
    """Yahoo asked this client to slow down; retry later."""


class YahooUnavailableError(ProviderError):
    """Yahoo or the optional yfinance dependency is unavailable."""


class YahooInvalidDataError(ProviderError):
    """Returned observations cannot be used without inventing or mixing data."""


class YahooFinanceProvider:
    provider_name = "yahoo-finance"

    def __init__(self, settings=None) -> None:
        self.settings = settings
        self.cache_root = (
            Path(getattr(settings, "artifact_storage_root", Path(".private/artifacts")))
            / "cache"
            / "yahoo"
        )
        self.cache_ttl_seconds = max(0, int(getattr(settings, "yahoo_cache_ttl_seconds", 3600)))
        self.max_retries = min(2, max(0, int(getattr(settings, "yahoo_max_retries", 1))))
        self.include_news = bool(getattr(settings, "yahoo_include_news", False))

    def fetch_company(self, ticker: str, refresh: bool = False) -> dict[str, Any]:
        """Fetch a single requested listing; never search for a substitute company.

        Bare symbols are interpreted as NSE symbols because this app targets
        India. Use an explicit .NS/.BO suffix (or another Yahoo listing symbol)
        to remove that assumption. Stale cache entries are never returned on
        provider failure. ``refresh=True`` bypasses a fresh cache entry too.
        """
        symbol, symbol_warning = _normalize_symbol(ticker)
        if not refresh:
            cached = self._read_cache(symbol)
            if cached is not None:
                if symbol_warning and symbol_warning not in cached["warnings"]:
                    cached["warnings"].append(symbol_warning)
                cached["cache_hit"] = True
                return cached

        try:
            import yfinance as yf
        except ImportError as exc:
            raise YahooUnavailableError(
                "Yahoo Finance requires the optional yfinance dependency. Install project dependencies."
            ) from exc

        # Vercel permits writes only in temporary storage. yfinance's cookie and
        # timezone cache is public provider state, never an owner document store.
        if hasattr(yf, "set_tz_cache_location"):
            import tempfile

            yf.set_tz_cache_location(str(Path(tempfile.gettempdir()) / "astra-yfinance"))
        company = yf.Ticker(symbol)
        info = self._call(company.get_info, symbol, "company profile")
        if not isinstance(info, Mapping):
            raise YahooInvalidDataError(f"Yahoo returned no valid company profile for {symbol}.")
        reported_symbol = str(info.get("symbol") or "").strip().upper()
        if reported_symbol != symbol:
            raise YahooInvalidDataError(
                f"Requested {symbol}, but Yahoo returned {reported_symbol or 'no verified symbol'}. "
                "No other company was substituted."
            )
        name = _text(info.get("longName")) or _text(info.get("shortName"))
        if not name:
            raise YahooInvalidDataError(f"Yahoo returned no company name for {symbol}.")
        currency = _currency(info.get("currency"))
        if currency is None:
            raise YahooInvalidDataError(
                f"Yahoo did not establish an unambiguous quote currency for {symbol}."
            )
        financial_currency = _currency(info.get("financialCurrency"))
        if financial_currency is not None and financial_currency != currency:
            raise YahooInvalidDataError(
                f"Yahoo quote currency {currency} differs from statement currency "
                f"{financial_currency} for {symbol}; no FX conversion is configured."
            )

        warnings = [
            "Yahoo Finance is a secondary source; verify financial statements against company filings."
        ]
        if symbol_warning:
            warnings.append(symbol_warning)
        statements: dict[str, dict[str, dict[str, Any]]] = {}
        statement_methods = {
            "income": company.get_income_stmt,
            "balance": company.get_balance_sheet,
            "cashflow": company.get_cashflow,
        }
        for kind, method in statement_methods.items():
            try:
                raw = self._call(
                    lambda method=method: method(as_dict=True, pretty=False, freq="yearly"),
                    symbol,
                    f"annual {kind} statement",
                )
                statements[kind] = _statement_periods(raw)
                if not statements[kind]:
                    warnings.append(f"Yahoo returned no annual {kind} statement for this listing.")
            except YahooRateLimitError:
                # A rate-limit response is actionable, not evidence of absent financials.
                raise
            except YahooUnavailableError as exc:
                statements[kind] = {}
                warnings.append(str(exc))

        periods = sorted({period for statement in statements.values() for period in statement})[-5:]
        history: dict[str, list] = {"years": [int(period[:4]) for period in periods]}
        metric_sources = {}
        for metric, (kind, aliases) in _METRICS.items():
            history[metric] = [
                _row_value(statements[kind].get(period, {}), aliases)
                if financial_currency
                else None
                for period in periods
            ]
            metric_sources[metric] = [
                next(
                    (
                        alias
                        for alias in aliases
                        if _row_value(statements[kind].get(period, {}), (alias,)) is not None
                    ),
                    None,
                )
                if financial_currency
                else None
                for period in periods
            ]
        if "EBIT" in metric_sources.get("ebit", []):
            warnings.append(
                "Operating income was unavailable for at least one fiscal period. Vendor EBIT is a proxy and may include non-operating income; reconcile it to the company filing before relying on the valuation."
            )
        if financial_currency is None:
            warnings.append(
                "Yahoo did not establish the financial-statement currency. "
                "Statement amounts are withheld instead of assuming the quote currency."
            )
        if any(value is not None and value < 0 for value in history["capital_expenditure"]):
            warnings.append("Capital expenditure is shown as a positive cash outflow.")
            history["capital_expenditure"] = [
                abs(value) if value is not None else None
                for value in history["capital_expenditure"]
            ]
        missing = [
            metric for metric in _METRICS if not any(value is not None for value in history[metric])
        ]
        if missing:
            warnings.append("Unavailable annual metrics remain null: " + ", ".join(missing) + ".")

        current_price = _positive(info.get("regularMarketPrice"))
        if current_price is None:
            current_price = _positive(info.get("currentPrice"))
        price_as_of = _timestamp(info.get("regularMarketTime")) if current_price else None
        if current_price is None:
            warnings.append("A valid current market price was not returned; price remains null.")
        elif price_as_of is None:
            warnings.append(
                "Yahoo did not supply a market-price timestamp; its as-of time is unknown."
            )
        if current_price is None and not any(value is not None for value in history["revenue"]):
            raise YahooInvalidDataError(
                f"Yahoo returned neither a usable market price nor verified annual revenue for {symbol}."
            )

        retrieved_at = datetime.now(UTC).isoformat()
        url = "https://finance.yahoo.com/quote/" + quote(symbol, safe="")
        beta_observation = (
            estimate_india_beta(company, yf, self.settings, symbol, refresh=refresh)
            if currency == "INR"
            else {}
        )
        published_beta = _number(info.get("beta"))
        beta = _number(beta_observation.get("value"))
        if beta is None:
            beta = published_beta
            beta_observation = {
                "value": beta,
                "as_of": None,
                "retrieved_at": retrieved_at,
                "method": "Yahoo published beta; benchmark and estimation window unverified",
                "benchmark": None,
                "observations": None,
                "r_squared": None,
                "source_url": url,
                "warnings": [
                    "A comparable independent beta estimate was unavailable. The vendor beta is an unverified proxy; review before relying on WACC."
                ],
            }
        warnings.extend(beta_observation.get("warnings", []))
        citations = [
            {
                "title": f"Yahoo Finance profile and quote: {symbol}",
                "uri": url,
                "provider": self.provider_name,
                "retrieved_label": retrieved_at,
            },
            {
                "title": f"Yahoo Finance annual financial statements: {symbol}",
                "uri": url + "/financials/",
                "provider": self.provider_name,
                "retrieved_label": retrieved_at,
            },
        ]
        result = {
            "name": name,
            "ticker": symbol,
            "sector": _text(info.get("sector")),
            "industry": _text(info.get("industry")),
            "business_summary": _text(info.get("longBusinessSummary")),
            "exchange": _text(info.get("exchange")),
            "currency": currency,
            "financial_currency": financial_currency,
            "website": _text(info.get("website")),
            "current_price": current_price,
            "price_as_of": price_as_of,
            "market_cap": _positive(info.get("marketCap")),
            "beta": beta,
            "published_beta": published_beta,
            "beta_observation": beta_observation,
            "shares_outstanding": _positive(info.get("sharesOutstanding")),
            "retrieved_at": retrieved_at,
            "financial_history": history,
            "metric_sources": metric_sources,
            "financial_basis": "Vendor annual statements; consolidation basis must be checked against company filings.",
            "fiscal_period_ends": periods,
            "citations": citations,
            "warnings": warnings,
            "news": self._news(company, symbol, warnings) if self.include_news else [],
            "cache_hit": False,
        }
        self._write_cache(symbol, result)
        return result

    def _call(self, operation: Callable[[], Any], symbol: str, description: str) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                rate_limited = type(exc).__name__ == "YFRateLimitError" or any(
                    term in str(exc).lower() for term in ("rate limit", "too many requests", "429")
                )
                if attempt < self.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 2))
                    continue
                error_type = YahooRateLimitError if rate_limited else YahooUnavailableError
                message = (
                    "rate limited this request; retry later"
                    if rate_limited
                    else "could not retrieve data"
                )
                raise error_type(f"Yahoo {message} for {symbol} ({description}).") from exc
        raise AssertionError("Unreachable retry state")

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_root / (sha256(symbol.encode("utf-8")).hexdigest() + ".json")

    def _read_cache(self, symbol: str) -> dict | None:
        if self.cache_ttl_seconds <= 0:
            return None
        try:
            payload = json.loads(self._cache_path(symbol).read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload["cached_at"])
            if cached_at.tzinfo is None:
                return None
            age = (datetime.now(UTC) - cached_at).total_seconds()
            result = payload["data"]
            if (
                payload["version"] != _CACHE_VERSION
                or not 0 <= age < self.cache_ttl_seconds
                or not _valid_cached_result(result, symbol)
            ):
                return None
            return result
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, symbol: str, result: dict) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        temporary = self._cache_path(symbol).with_suffix(f".{uuid4().hex}.tmp")
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    {
                        "version": _CACHE_VERSION,
                        "cached_at": datetime.now(UTC).isoformat(),
                        "data": result,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self._cache_path(symbol))
        except (OSError, ValueError, TypeError):
            result["warnings"].append("Yahoo observations could not be cached locally.")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _news(self, company, symbol: str, warnings: list[str]) -> list[dict]:
        try:
            raw = self._call(lambda: company.get_news(count=5), symbol, "news")
            news = []
            for item in raw or []:
                content = item.get("content") or item
                canonical = content.get("canonicalUrl") or {}
                uri = canonical.get("url") or content.get("link")
                if content.get("title") and isinstance(uri, str) and uri.startswith("https://"):
                    news.append(
                        {
                            "title": str(content["title"]),
                            "uri": uri,
                            "published_at": _timestamp(
                                content.get("pubDate") or content.get("providerPublishTime")
                            ),
                        }
                    )
            return news
        except (ProviderError, AttributeError, TypeError):
            warnings.append("Optional Yahoo news could not be retrieved.")
            return []


def _normalize_symbol(ticker: str) -> tuple[str, str | None]:
    if not isinstance(ticker, str):
        raise YahooInvalidDataError("A Yahoo listing symbol is required.")
    symbol = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&.\-]{0,39}", symbol):
        raise YahooInvalidDataError(
            "Use a single listing symbol, for example RELIANCE.NS or 500325.BO."
        )
    if "." not in symbol:
        return symbol + ".NS", (
            f"Bare ticker {symbol} was interpreted as NSE listing {symbol}.NS. "
            "Verify the returned company identity; specify .BO for a BSE listing."
        )
    return symbol, None


def _valid_cached_result(result: Any, symbol: str) -> bool:
    if (
        not isinstance(result, dict)
        or result.get("ticker") != symbol
        or not _text(result.get("name"))
    ):
        return False
    if _currency(result.get("currency")) is None:
        return False
    financial_currency = result.get("financial_currency")
    if financial_currency is not None and financial_currency != result["currency"]:
        return False
    if not _timestamp(result.get("retrieved_at")):
        return False
    if not isinstance(result.get("warnings"), list) or not all(
        isinstance(warning, str) for warning in result["warnings"]
    ):
        return False
    if not isinstance(result.get("citations"), list) or not isinstance(result.get("news"), list):
        return False
    for key in ("current_price", "market_cap", "shares_outstanding"):
        if key not in result or (result[key] is not None and _positive(result[key]) is None):
            return False
    history = result.get("financial_history")
    periods = result.get("fiscal_period_ends")
    if not isinstance(history, dict) or not isinstance(periods, list):
        return False
    try:
        parsed_periods = [date.fromisoformat(period) for period in periods]
    except (TypeError, ValueError):
        return False
    if periods != sorted(set(periods)) or history.get("years") != [d.year for d in parsed_periods]:
        return False
    for metric in _METRICS:
        values = history.get(metric)
        if not isinstance(values, list) or len(values) != len(periods):
            return False
        if any(value is not None and _number(value) is None for value in values):
            return False
        if financial_currency is None and any(value is not None for value in values):
            return False
    return True


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _currency(value: Any) -> str | None:
    # Lowercase suffixes such as GBp denote minor units, not the ISO currency GBP.
    text = _text(value)
    return text if text and re.fullmatch(r"[A-Z]{3}", text) else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _timestamp(value: Any) -> str | None:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _statement_periods(raw: Any) -> dict[str, dict[str, Any]]:
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()
    if not isinstance(raw, Mapping):
        return {}
    periods = {}
    for column, values in raw.items():
        try:
            period = (
                column.date()
                if isinstance(column, datetime)
                else (column if isinstance(column, date) else date.fromisoformat(str(column)[:10]))
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(values, Mapping):
            continue
        periods[period.isoformat()] = {
            re.sub(r"[\s_]", "", str(key)).lower(): value for key, value in values.items()
        }
    return periods


def _row_value(row: dict[str, Any], aliases: tuple[str, ...]) -> float | None:
    for alias in aliases:
        value = _number(row.get(re.sub(r"[\s_]", "", alias).lower()))
        if value is not None:
            return value
    return None

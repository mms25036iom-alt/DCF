"""Offline official-source fixtures verify rates, dates, and bounded failures."""

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from openpyxl import Workbook

from research.providers import market_assumptions as market

TODAY = date(2026, 10, 29)
XLSX_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx"
RBI_HTML = b"""<h3>Government Securities Market</h3><div><table>
<tr><th>6.36% GS 2031</th><td>: 7.2013% #</td></tr>
<tr><th>6.94% GS 2036</th><td>: 7.4389% #</td></tr>
<tr><th>7.06% GS 2041</th><td>: 7.6075% #</td></tr>
<tr><th>364 day T-bills</th><td>: 6.6900%*</td></tr></table>
<div>* cut-off at the last auction</div>
<div><span>#</span><span> as on <!--January 27, 2026--> October 28, 2026</span></div>
</div>"""
INDEX_HTML = b"""<a href='../pc/datasets/ctryprem.xlsx'>Undated old table</a>
<a href='https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJan26.xlsx'>January 1, 2026 update</a>
<a href='https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx'>July 1, 2026 update</a>"""


def workbook_bytes(erp=0.06920182533929162, spread=0.01749881223830583):
    book = Workbook()
    sheet = book.active
    sheet.title = "ERPs by country"
    sheet["A2"], sheet["B2"] = "Date of update:", datetime(2022, 7, 1)
    sheet.append([])
    sheet.append([])
    sheet.append(["Country", "Region", "Moody's rating", "Rating-based Default Spread",
                  "Total Equity Risk Premium", "Country Risk Premium", "CDS",
                  "Total Equity Risk Premium2"])
    sheet.append(["India", "Asia", "Baa3", spread, erp, 0.0272, 0.0072, 0.05319])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


@pytest.fixture
def fixture_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "_today", lambda: TODAY)
    responses = {
        market.RBI_URL: httpx.Response(200, content=RBI_HTML),
        market.NYU_INDEX_URL: httpx.Response(200, content=INDEX_HTML),
        XLSX_URL: httpx.Response(200, content=workbook_bytes()),
    }
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return responses[str(request.url)]

    original = httpx.Client
    monkeypatch.setattr(market.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    settings = SimpleNamespace(artifact_storage_root=tmp_path)
    return settings, responses, calls


def test_dated_official_rates_are_adjusted_and_country_erp_column_is_exact(fixture_sources):
    settings, _, calls = fixture_sources
    result = market.fetch_india_market_assumptions(settings)
    assert result["status"] == "complete"
    assert result["sovereign_yield"]["value"] == pytest.approx(0.074389)
    assert result["risk_free_rate"]["value"] == pytest.approx(0.074389 - 0.01749881223830583)
    assert result["equity_risk_premium"]["value"] == pytest.approx(0.06920182533929162)
    assert result["equity_risk_premium"]["as_of"] == "2026-07-01"
    assert result["risk_free_rate"]["as_of"] == "2026-10-28"
    assert result["risk_free_rate"]["adjustment_as_of"] == "2026-07-01"
    assert "estimate" in result["risk_free_rate"]["basis"]
    assert any("date cell differs" in warning for warning in result["warnings"])
    assert len(result["citations"]) == 2
    assert calls == [market.RBI_URL, market.NYU_INDEX_URL, XLSX_URL]


def test_complete_cache_is_reused_and_refresh_bypasses_it(fixture_sources):
    settings, responses, calls = fixture_sources
    first = market.fetch_india_market_assumptions(settings)
    second = market.fetch_india_market_assumptions(settings)
    assert second["cache_hit"] is True
    assert second["risk_free_rate"] == first["risk_free_rate"]
    assert len(calls) == 3
    responses[market.RBI_URL] = httpx.Response(503)
    refreshed = market.fetch_india_market_assumptions(settings, refresh=True)
    assert refreshed["risk_free_rate"]["value"] is None
    assert refreshed["status"] == "partial"
    assert refreshed["cache_hit"] is False


def test_stale_or_future_rbi_dates_never_become_current_rates(fixture_sources):
    settings, responses, _ = fixture_sources
    for stamp in (b"September 28, 2026", b"October 30, 2026"):
        responses[market.RBI_URL] = httpx.Response(200, content=RBI_HTML.replace(b"October 28, 2026", stamp))
        result = market.fetch_india_market_assumptions(settings, refresh=True)
        assert result["risk_free_rate"]["value"] is None
        assert result["equity_risk_premium"]["value"] is not None


def test_missing_spread_uses_explicit_unadjusted_sovereign_proxy(fixture_sources):
    settings, responses, _ = fixture_sources
    responses[XLSX_URL] = httpx.Response(200, content=workbook_bytes(spread=None))
    result = market.fetch_india_market_assumptions(settings)
    assert result["status"] == "partial"
    assert result["risk_free_rate"]["value"] == pytest.approx(0.074389)
    assert "Unadjusted" in result["risk_free_rate"]["label"]
    assert result["equity_risk_premium"]["value"] is None
    assert not market._cache_path(settings).exists()


def test_redirects_and_errors_do_not_escape_or_leak_response_secrets(fixture_sources):
    settings, responses, calls = fixture_sources
    responses[market.RBI_URL] = httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
    responses[market.NYU_INDEX_URL] = httpx.Response(403, text="credential=secret-value")
    result = market.fetch_india_market_assumptions(settings)
    assert result["status"] == "unavailable"
    assert result["risk_free_rate"]["value"] is None
    assert result["equity_risk_premium"]["value"] is None
    assert calls == [market.RBI_URL, market.NYU_INDEX_URL]
    assert "secret-value" not in json.dumps(result)


@pytest.mark.parametrize("url", [
    "http://www.rbi.org.in/", "https://www.rbi.org.in.attacker.org/",
    "https://127.0.0.1/", "https://user:password@www.rbi.org.in/",
    "https://pages.stern.nyu.edu:444/", "https://pages.stern.nyu.edu/\\evil",
])
def test_unapproved_urls_are_rejected_before_connection(url):
    class NoNetwork:
        def stream(self, *args, **kwargs):
            pytest.fail("Unapproved URL must never connect")

    with pytest.raises(ValueError):
        market._download(NoNetwork(), url, 1024, market.time.monotonic() + 10)


def test_discovery_picks_newest_dated_link_and_excludes_future_or_foreign_links():
    body = INDEX_HTML + b"""
    <a href='https://attacker.org/ctrypremOct26.xlsx'>October 1, 2026 update</a>
    <a href='https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJan27.xlsx'>January 1, 2027 update</a>
    <a href='https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremOct26.xlsx'>October 1, 2026 update</a>"""
    url, as_of = market._latest_workbook(body, TODAY)
    assert url.endswith("ctrypremOct26.xlsx")
    assert as_of == date(2026, 10, 1)
    with pytest.raises(ValueError):
        market._latest_workbook(INDEX_HTML, date(2029, 10, 29))


def test_download_and_xlsx_expansion_are_bounded(fixture_sources, monkeypatch):
    settings, responses, _ = fixture_sources
    responses[market.RBI_URL] = httpx.Response(200, content=RBI_HTML,
                                             headers={"content-length": str(market._HTML_LIMIT + 1)})
    result = market.fetch_india_market_assumptions(settings)
    assert result["sovereign_yield"]["value"] is None
    monkeypatch.setattr(market, "_EXPANDED_LIMIT", 10)
    with pytest.raises(ValueError, match="expanded-size"):
        market._country_premiums(workbook_bytes(), XLSX_URL, date(2026, 7, 1))


def test_workbook_xml_entities_are_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", '<!DOCTYPE x [<!ENTITY evil SYSTEM "file:///x">]><x/>')
    with pytest.raises(ValueError, match="XML declarations"):
        market._country_premiums(buffer.getvalue(), XLSX_URL, date(2026, 7, 1))


def test_expired_cache_and_stale_source_dates_are_refetched(fixture_sources):
    settings, _, calls = fixture_sources
    market.fetch_india_market_assumptions(settings)
    path = market._cache_path(settings)
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["retrieved_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(cached), encoding="utf-8")
    assert market.fetch_india_market_assumptions(settings)["cache_hit"] is False
    assert len(calls) == 6
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["sovereign_yield"]["as_of"] = "2026-08-01"
    path.write_text(json.dumps(cached), encoding="utf-8")
    assert market.fetch_india_market_assumptions(settings)["cache_hit"] is False
    assert len(calls) == 9

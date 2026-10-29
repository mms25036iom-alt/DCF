from io import BytesIO

from pypdf import PdfReader

from research.reporting.live_report import render_live_report
from research.reporting.pdf_report import render_report_pdf


def test_report_pdf_contains_real_company_units_and_valuation_context():
    state = {
        "selected_company": "Observed Industries",
        "selected_ticker": "OBSERVED.NS",
        "currency": "INR",
        "exchange": "NSI",
        "data_mode": "LIVE",
        "current_price": 501.25,
        "price_date": "2026-10-27T10:00:00+00:00",
        "financial_history": {"years": [2024, 2025], "revenue": [1e9, 1.2e9], "ebit": [2e8, 2.4e8]},
        "metrics": {"ebit_margin_percent": 20},
        "snapshots": [{"ticker": "OBSERVED.NS", "business_summary": "Makes industrial products."}],
        "stock_model": {
            "withheld_reason": "Cash-flow observations are incomplete.",
            "forecast": [],
            "valuation": None,
        },
    }
    content = render_report_pdf(render_live_report(state))
    assert content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Observed Industries" in text and "OBSERVED.NS" in text
    assert "INR crore" in text and "120.00" in text
    assert "501.25" in text
    assert "Cash-flow observations are incomplete" in text
    assert "NOT RATED" in text
    assert "Page 1" in reader.pages[0].extract_text()
    assert reader.metadata.title == "Observed Industries - Equity research"


def test_pdf_safely_renders_markup_and_allows_only_web_links():
    source = """# Astra Research Report
**Company:** Markup Company
**Ticker:** MARKUP.NS
**Data Mode:** LIVE

## Evidence
<script>alert('untrusted')</script>

[Primary source](https://example.org/report?q=1&year=2025)
[Invalid action](javascript:alert(1))
![Remote image](https://example.org/tracking.png)

> Source excerpt with <b>untrusted tags</b> & ordinary text.

**Bold observation** and *bounded interpretation*.
"""
    reader = PdfReader(BytesIO(render_report_pdf(source)))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Markup Company" in text and "untrusted" in text
    assert "Bold observation" in text and "Remote image" in text
    destinations = []
    for page in reader.pages:
        for reference in page.get("/Annots", []):
            action = reference.get_object().get("/A")
            if action and action.get("/URI"):
                destinations.append(str(action["/URI"]))
    assert destinations == ["https://example.org/report"]


def test_pdf_redacts_private_and_credential_locations_even_in_raw_markdown():
    source = "# Astra Research Report\n**Company:** Privacy Test\n**Display units:** INR crore\n\n## Sources\n\n"
    source += r"Private C:\Users\private\report.pdf and local://private/report.pdf"
    source += "\n\n[Unsafe](https://user:password@example.org/report) [Public](https://example.org/report?token=secret)"
    reader = PdfReader(BytesIO(render_report_pdf(source)))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "password" not in text and "secret" not in text and "C:\\Users" not in text
    assert "INR crore" in text
    for page in reader.pages:
        for reference in page.get("/Annots", []):
            action = reference.get_object().get("/A")
            if action and action.get("/URI"):
                assert str(action["/URI"]) == "https://example.org/report"


def test_pdf_long_financial_tables_repeat_headers_across_pages():
    source = "# Astra Research Report\n**Company:** Table Company\n**Ticker:** TABLE.NS\n\n## Financial performance\n\n| Financial metric | 2024 | 2025 | 2026 |\n| --- | --- | --- | --- |\n"
    source += "\n".join(
        f"| Observed metric {index} | 1,000.00 | N/A | 1,200.00 |" for index in range(100)
    )
    reader = PdfReader(BytesIO(render_report_pdf(source)))
    assert len(reader.pages) >= 3
    table_pages = [
        page.extract_text() for page in reader.pages if "Observed metric" in page.extract_text()
    ]
    assert len(table_pages) >= 3
    assert all("Financial metric" in text for text in table_pages)
    assert "Observed metric 99" in reader.pages[-1].extract_text()
    assert f"Page {len(reader.pages)}" in reader.pages[-1].extract_text()


def test_pdf_currency_symbols_and_long_sources_do_not_break_layout():
    markdown = "# Astra Research Report\n**Company:** Test Company\n\n## Valuation\n\n₹ 1,000 crore – operating cash flow × forecast multiple.\n\n"
    markdown += "Source: https://example.org/" + "long-filings-path-" * 35
    text = "\n".join(
        page.extract_text() for page in PdfReader(BytesIO(render_report_pdf(markdown))).pages
    )
    assert "INR" in text and "1,000 crore" in text
    assert "operating cash flow x forecast multiple" in text


def test_compact_report_cover_uses_observed_exchange_and_citations_are_clickable():
    state = {
        "stock_model": {
            "source_snapshot": {
                "name": "Observed Company",
                "ticker": "OBSERVED.NS",
                "currency": "INR",
                "exchange": "NSI",
                "citations": [
                    {
                        "title": "Observed annual statements",
                        "uri": "https://example.org/filing?token=secret123#page-2",
                        "provider": "observed provider",
                    }
                ],
            }
        }
    }
    reader = PdfReader(BytesIO(render_report_pdf(render_live_report(state))))
    cover = reader.pages[0].extract_text()
    assert "NSI" in cover and "Not verified" not in cover
    destinations = [
        str(reference.get_object()["/A"]["/URI"])
        for page in reader.pages
        for reference in page.get("/Annots", [])
        if reference.get_object().get("/A", {}).get("/URI")
    ]
    assert destinations == ["https://example.org/filing"]
    assert "secret123" not in "\n".join(page.extract_text() for page in reader.pages)


def test_small_schedule_stays_with_its_heading_and_intro_near_page_boundary():
    markdown = "# Astra Research Report\n**Company:** Boundary Company\n\n"
    markdown += "\n\n".join("Preceding operating discussion." for _ in range(17))
    markdown += "\n\n### Compact schedule\n\nForecast amounts are estimates in INR crore.\n\n"
    markdown += "| Driver | Value |\n| --- | --- |\n"
    markdown += "\n".join(f"| Schedule row {index} | {index + 10}.00 |" for index in range(6))
    reader = PdfReader(BytesIO(render_report_pdf(markdown)))
    schedule_pages = [
        page.extract_text() for page in reader.pages if "Schedule row" in page.extract_text()
    ]
    assert len(schedule_pages) == 1
    assert "Schedule row 0" in schedule_pages[0] and "Schedule row 5" in schedule_pages[0]
    assert "Compact schedule" in schedule_pages[0]
    assert "Forecast amounts are estimates in INR crore" in schedule_pages[0]


def test_pdf_retains_dated_dcf_timing_terminal_and_review_disclosures():
    from research.data.stock_model import build_stock_model
    from tests.test_stock_model import stock_observation as observation_fixture

    snapshot = observation_fixture.__wrapped__()
    snapshot["price_as_of"] = "2025-10-15"
    snapshot["market_assumptions"] = {
        "risk_free_rate": {
            "value": 0.065,
            "as_of": "2025-10-14",
            "source_url": "https://example.org/sovereign",
        },
    }
    model = build_stock_model(snapshot)
    reader = PdfReader(BytesIO(render_report_pdf(render_live_report({"stock_model": model}))))
    text = "\n".join(page.extract_text() for page in reader.pages)
    normalized_text = " ".join(text.split())
    for disclosure in (
        "2025-10-15",
        "2025-10-14",
        "Terminal ROIC",
        "Terminal reinvestment",
        "Terminal FCFF",
        "book-value proxy",
        "Cash-flow fraction after valuation date",
        "Input review status",
        "no excess returns",
    ):
        assert disclosure in normalized_text
    destinations = [
        str(reference.get_object()["/A"]["/URI"])
        for page in reader.pages
        for reference in page.get("/Annots", [])
        if reference.get_object().get("/A", {}).get("/URI")
    ]
    assert "https://example.org/sovereign" in destinations

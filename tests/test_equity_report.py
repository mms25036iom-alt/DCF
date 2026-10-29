from copy import deepcopy

import pytest

from research.reporting.live_report import render_live_report
from research.reporting.units import format_amount, format_number, format_percent, resolve_unit


@pytest.fixture
def equity_state():
    snapshot = {
        "name": "Observed Industries",
        "ticker": "OBSERVED.NS",
        "sector": "Industrials",
        "industry": "Industrial products",
        "business_summary": "Makes observed industrial products.",
        "website": "https://example.org",
        "currency": "INR",
        "market_cap": 5_000_000_000,
        "shares_outstanding": 10_000_000,
        "retrieved_at": "2026-10-27T10:00:00+00:00",
    }
    return {
        "selected_company": snapshot["name"],
        "selected_ticker": snapshot["ticker"],
        "sector": "Industrials",
        "exchange": "NSI",
        "currency": "INR",
        "data_mode": "LIVE",
        "current_price": 501.25,
        "price_date": "2026-10-27T09:30:00+00:00",
        "snapshots": [snapshot],
        "fiscal_period_ends": ["2024-03-31", "2025-03-31"],
        "financial_history": {
            "years": [2024, 2025],
            "revenue": [1_000_000_000, 1_200_000_000],
            "ebit": [200_000_000, 240_000_000],
            "net_income": [100_000_000, None],
            "operating_cash_flow": [180_000_000, 200_000_000],
            "capital_expenditure": [60_000_000, 60_000_000],
            "shareholders_equity": [500_000_000, 600_000_000],
            "total_debt": [100_000_000, 100_000_000],
            "cash_and_equivalents": [50_000_000, 50_000_000],
        },
        "metrics": {
            "revenue_cagr_percent": 20,
            "ebit_margin_percent": 20,
            "operating_cash_flow_less_capex": 140_000_000,
        },
        "citations": [
            {
                "title": "Observed financials",
                "uri": "https://example.org/filing",
                "provider": "fixture",
                "retrieved_label": "2026-10-27",
            }
        ],
    }


def test_presentation_units_do_not_change_currency_or_scale_per_share(equity_state):
    report = render_live_report(equity_state)
    assert "Financial amounts in **INR crore**" in report
    assert "| Revenue | 100.00 | 120.00 |" in report
    assert "| Last available price (INR, absolute) | 501.25 |" in report
    assert "| Net profit | 10.00 | N/A |" in report
    assert "Operating cash flow less capital expenditure: 14.00 INR crore" in report
    assert "| EBIT margin | 20.00% | 20.00% |" in report
    assert "**Rating:** NOT RATED" in report
    assert "Makes observed industrial products." in report
    assert "DEMO" not in report


def test_explicit_lakh_and_non_inr_million_display(equity_state):
    lakh = render_live_report({**equity_state, "display_unit": "lakh"})
    assert "Financial amounts in **INR lakh**" in lakh
    assert "| Revenue | 10,000.00 | 12,000.00 |" in lakh
    usd = render_live_report({**equity_state, "currency": "USD"})
    assert "Financial amounts in **USD million**" in usd
    assert "| Revenue | 1,000.00 | 1,200.00 |" in usd
    assert "No foreign-exchange conversion is performed" in usd


def test_missing_ratios_and_empty_history_never_become_zero(equity_state):
    report = render_live_report(equity_state)
    assert "| Net profit margin | 10.00% | N/A |" in report
    assert "| Revenue growth (YoY) | N/A | 20.00% |" in report
    empty = render_live_report(
        {"data_mode": "LIVE", "abstained": True, "abstain_reason": "Ticker unavailable"}
    )
    assert "Research abstained" in empty and "Ticker unavailable" in empty
    assert "Comparable annual statements were not available" in empty
    assert "No supported scenario valuation was produced" in empty


def test_stock_model_bridge_and_sensitivity_keep_per_share_absolute(equity_state):
    state = deepcopy(equity_state)
    state["stock_model"] = {
        "assumptions": {
            "revenue_growth": 0.1,
            "discount_rate": 0.12,
            "terminal_growth": 0.03,
            "net_debt": 50_000_000,
            "shares_outstanding": 10_000_000,
        },
        "forecast": [
            {
                "year": 2026,
                "revenue": 1_320_000_000,
                "ebit": 264_000_000,
                "nopat": 198_000_000,
                "free_cash_flow": 140_000_000,
            }
        ],
        "valuation": {
            "present_value_fcff": 500_000_000,
            "present_value_terminal": 1_500_000_000,
            "enterprise_value": 2_000_000_000,
            "net_debt": 50_000_000,
            "equity_value": 1_950_000_000,
            "shares_outstanding": 10_000_000,
            "per_share": 195,
            "current_price": 501.25,
            "upside_percent": -61.097,
        },
        "sensitivity": {
            "discount_rates": [0.12],
            "terminal_growth_rates": [0.03, 0.12],
            "values": [[195, None]],
        },
    }
    report = render_live_report(state)
    assert "| Revenue | 132.00 |" in report
    assert "| Enterprise value | 200.00 |" in report
    assert "| Illustrative value per share (INR, absolute) | 195.00 |" in report
    assert "| Scenario difference versus observed price | -61.10% |" in report
    assert "| 12.00% | 195.00 | N/A |" in report
    assert "1,320,000,000" not in report


def test_withheld_bank_model_never_falls_back_to_legacy_valuation(equity_state):
    state = {
        **equity_state,
        "stock_model": {
            "withheld_reason": "Operating-company DCF is unsuitable for banks.",
            "forecast": [],
            "valuation": None,
        },
        "valuation": {"method": "Legacy", "scenarios": {"base": 99999}},
    }
    report = render_live_report(state)
    assert "**Valuation withheld:** Operating-company DCF is unsuitable for banks." in report
    assert "99,999" not in report and "Legacy" not in report


def test_report_bridge_discloses_minority_adjustment_and_terminal_calculation(equity_state):
    model = {
        "valuation": {
            "present_value_fcff": 500_000_000,
            "terminal_value": 2_500_000_000,
            "present_value_terminal": 1_500_000_000,
            "enterprise_value": 2_000_000_000,
            "net_debt": 50_000_000,
            "noncontrolling_interest": 200_000_000,
            "equity_value": 1_750_000_000,
            "shares_outstanding": 10_000_000,
            "per_share": 175,
        }
    }
    report = render_live_report({**equity_state, "stock_model": model})
    assert "| Terminal value at forecast end (undiscounted) | 250.00 |" in report
    assert "| Enterprise value | 200.00 |" in report
    assert "| Less: net debt / (cash) | 5.00 |" in report
    assert "| Less: reported non-controlling interests | 20.00 |" in report
    assert "| Equity value | 175.00 |" in report
    assert "Terminal value = terminal NOPAT" in report
    assert "book-value proxy" in report
    assert "Missing bridge adjustment" not in report
    model["valuation"]["noncontrolling_interest"] = 0
    zero_report = render_live_report({**equity_state, "stock_model": model})
    assert "| Less: reported non-controlling interests | 0.00 |" in zero_report
    assert "Missing bridge adjustment" not in zero_report
    model["valuation"]["noncontrolling_interest"] = None
    missing_report = render_live_report({**equity_state, "stock_model": model})
    assert "| Less: reported non-controlling interests | N/A |" in missing_report
    assert "N/A does not establish a zero balance" in missing_report


def test_selected_primary_and_quoted_document_preserve_provenance(equity_state):
    state = deepcopy(equity_state)
    state["snapshots"].insert(
        0, {"ticker": "OTHER.NS", "business_summary": "Wrong company profile"}
    )
    state["source_contexts"] = [
        {
            "title": "annual.csv",
            "source_version_id": "source-123",
            "content": "CSV!B2 Revenue\n## Source heading",
        }
    ]
    report = render_live_report(state)
    assert "Wrong company profile" not in report
    assert "Makes observed industrial products." in report
    assert "> CSV!B2 Revenue" in report and "> ## Source heading" in report
    assert "Source version: source-123" in report


def test_unit_formatters_reject_nonfinite_missing_and_boolean_values():
    crore = resolve_unit("INR")
    assert format_amount(10_000_000, crore) == "1.00"
    assert resolve_unit("USD")["divisor"] == 1_000_000
    assert resolve_unit("INR", "absolute")["divisor"] == 1
    assert format_number(float("inf")) == "N/A"
    assert format_number(True) == "N/A"
    assert format_percent(None) == "N/A"
    assert format_percent(0.125, ratio=True) == "12.50%"


def test_report_hides_private_locations_and_credential_urls(equity_state):
    state = deepcopy(equity_state)
    state["citations"] = [
        {"title": "Public filing", "uri": "https://example.org/report?api_key=secret123#auth"},
        {"title": "Private file", "uri": "local://C:/Users/private/report.pdf"},
        {"title": "Invalid credentials", "uri": "https://user:secret123@example.org/private"},
        {"title": "Internal link", "uri": "http://169.254.169.254/latest/meta-data"},
    ]
    state["source_contexts"] = [
        {"title": "Disclosure", "content": r"Path C:\Users\private\report.pdf"}
    ]
    report = render_live_report(state)
    assert "secret123" not in report and "C:\\Users" not in report and "local://" not in report
    assert "169.254.169.254" not in report
    assert "https://example.org/report" in report


def test_data_only_report_omits_repetitive_role_templates_and_legacy_values(equity_state):
    state = deepcopy(equity_state)
    state["agent_outputs"] = {
        "risk": {
            "generation": "data-only",
            "agent_name": "Risk Analyst",
            "executive_findings": "Repeated generic role template",
        }
    }
    state["valuation"] = {"scenarios": {"base": 99999999}}
    report = render_live_report(state)
    assert "Repeated generic role template" not in report
    assert "99,999,999" not in report
    assert "Specialist research review" not in report
    assert "**Display units:** INR crore" in report


def test_report_contains_linked_wacc_and_working_capital_schedules(equity_state):
    state = deepcopy(equity_state)
    state["stock_model"] = {
        "wacc": {
            "method": "capm",
            "inputs": {
                "risk_free_rate": 0.06,
                "equity_risk_premium": 0.05,
                "beta": 1.2,
                "pretax_cost_of_debt": 0.08,
                "debt_weight": 0.2,
            },
            "cost_of_equity": 0.12,
            "after_tax_cost_of_debt": 0.06,
            "calculated_wacc": 0.108,
            "active_wacc": 0.108,
        },
        "working_capital": {
            "method": "operating_days",
            "base_observations": True,
            "drivers": {
                "receivable_days": 30,
                "inventory_days": 40,
                "payable_days": 50,
                "cogs_rate": 0.6,
            },
            "forecast": [
                {
                    "year": 2026,
                    "receivables": 100_000_000,
                    "inventory": 80_000_000,
                    "payables": 90_000_000,
                    "working_capital_balance": 90_000_000,
                    "change_in_working_capital": 10_000_000,
                }
            ],
        },
    }
    report = render_live_report(state)
    assert "Cost of capital build" in report and "| Calculated WACC | 10.80%" in report
    assert "Operating working-capital schedule" in report
    assert "| Trade receivables | 10.00 |" in report
    assert "base balance uses reported same-period components" in report


def test_single_model_note_is_labeled_and_does_not_claim_data_only(equity_state):
    report = render_live_report(
        {
            **equity_state,
            "analyst_note": {"provider": "groq", "text": "An evidence-grounded assessment."},
        }
    )
    assert "**Analysis performed:** groq" in report
    assert "An evidence-grounded assessment." in report and "## Research synthesis" in report


def test_report_discloses_normalization_dated_rates_stub_and_terminal_reinvestment(equity_state):
    from research.data.stock_model import build_stock_model
    from tests.test_stock_model import stock_observation as observation_fixture

    snapshot = observation_fixture.__wrapped__()
    snapshot["price_as_of"] = "2025-10-15"
    snapshot["market_assumptions"] = {
        "risk_free_rate": {
            "value": 0.065,
            "as_of": "2025-10-14",
            "source_url": "https://example.org/sovereign?token=private",
        },
        "equity_risk_premium": {
            "value": 0.06,
            "as_of": "2025-10-01",
            "source_url": "https://example.org/erp",
        },
    }
    model = build_stock_model(snapshot)
    report = render_live_report({**equity_state, "stock_model": model})
    for disclosure in (
        "2025-10-15",
        "2025-10-14",
        "2025-10-01",
        "https://example.org/sovereign",
        "median of the last three valid",
        "2024-03-31, 2025-03-31",
        "fades linearly",
        "First forecast revenue growth",
        "Fifth forecast revenue growth",
        "actual days / 365.25",
        "Cash-flow fraction after valuation date",
        "FCFF after valuation date",
        "Negative EBIT receives no automatic cash-tax refund",
        "Terminal reinvestment",
        "Terminal FCFF",
        "no excess returns",
        "book-value proxy",
        "Input review status",
        "have not been independently verified or audited",
        "Default ROIC follows each row's WACC",
    ):
        assert disclosure in report
    assert "token=private" not in report
    assert "final forecast FCFF × (1 + terminal growth)" not in report


def test_report_preserves_quality_findings_when_stale_coverage_withholds(equity_state):
    report = render_live_report(
        {
            **equity_state,
            "stock_model": {
                "withheld_reason": "DCF withheld: stale annual coverage.",
                "valuation_date": "2026-10-29",
                "forecast": [],
                "valuation": None,
                "quality": {
                    "status": "needs_review",
                    "issues": ["New annual financial statements are required."],
                },
            },
        }
    )
    assert "**Valuation withheld:** DCF withheld: stale annual coverage." in report
    assert "New annual financial statements are required." in report
    assert "**Input review status:** needs_review" in report

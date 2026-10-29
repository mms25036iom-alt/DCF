from copy import deepcopy
from datetime import date

import pytest

from research.data.stock_model import build_stock_model


@pytest.fixture
def stock_observation():
    return {
        "name": "Verified Industrial Company",
        "ticker": "REAL.NS",
        "currency": "INR",
        "financial_currency": "INR",
        "sector": "Industrials",
        "current_price": 120,
        "shares_outstanding": 100_000_000,
        "retrieved_at": "2025-03-31T12:00:00+00:00",
        "price_as_of": "2025-03-31T10:00:00+00:00",
        "fiscal_period_ends": ["2024-03-31", "2025-03-31"],
        "financial_history": {
            "years": [2024, 2025],
            "revenue": [10_000_000_000, 12_000_000_000],
            "ebit": [2_000_000_000, 2_400_000_000],
            "depreciation": [300_000_000, 360_000_000],
            "capital_expenditure": [500_000_000, 600_000_000],
            "total_debt": [1_000_000_000, 1_100_000_000],
            "cash_and_equivalents": [500_000_000, 600_000_000],
            "net_income": [1_400_000_000, 1_700_000_000],
            "income_tax": [500_000_000, 600_000_000],
            "pretax_income": [2_000_000_000, 2_400_000_000],
        },
        "citations": [
            {
                "title": "Observed annuals",
                "uri": "https://example.org/financials",
                "retrieved_label": "2026-10-27",
            }
        ],
        "warnings": [],
    }


def test_material_normalization_cash_release_is_flagged(stock_observation):
    stock_observation["financial_history"].update(
        accounts_receivable=[1_000_000_000, 4_000_000_000],
        inventory=[1_000_000_000, 1_200_000_000],
        accounts_payable=[1_000_000_000, 1_200_000_000],
        cost_of_revenue=[7_000_000_000, 8_400_000_000],
    )
    model = build_stock_model(stock_observation)
    assert model["forecast"][0]["change_in_working_capital"] < -0.025 * 12_000_000_000
    assert any("material working-capital cash release" in issue for issue in model["quality"]["issues"])
    assert model["working_capital"]["base_balance"] == 4_000_000_000


def test_stock_model_uses_actuals_and_matches_discounted_cash_flows(stock_observation):
    model = build_stock_model(stock_observation)
    assert model["model_type"] == "LIVE" and model["company"] == stock_observation["name"]
    assert model["base_period"] == "2025-03-31"
    assert model["unit"]["divisor"] == 10_000_000
    inputs = model["assumptions"]
    assert inputs["revenue_growth"] == pytest.approx(0.2)
    assert inputs["ebit_margin"] == 0.2
    first = model["forecast"][0]
    assert first["revenue"] == pytest.approx(12_000_000_000 * 1.2)
    assert first["change_in_working_capital"] == pytest.approx(
        (first["revenue"] - 12_000_000_000) * 0.02
    )
    terminal = model["terminal"]
    expected_terminal_fcff = terminal["nopat"] * (1 - inputs["terminal_growth"] / inputs["discount_rate"])
    terminal_value = expected_terminal_fcff / (inputs["discount_rate"] - inputs["terminal_growth"])
    present_terminal = terminal_value / (1 + inputs["discount_rate"]) ** model["forecast"][-1]["discount_period"]
    expected_equity = sum(row["present_value"] for row in model["forecast"]) + present_terminal - 500_000_000
    assert model["valuation"]["per_share"] == pytest.approx(expected_equity / 100_000_000)
    assert model["sensitivity"]["values"][2][2] == pytest.approx(model["valuation"]["per_share"])
    assert model["source_snapshot"] == stock_observation


def test_display_units_do_not_change_valuation_or_share_price(stock_observation):
    models = [
        build_stock_model(stock_observation, display_unit=unit)
        for unit in ("crore", "lakh", "million", "absolute")
    ]
    assert {model["unit"]["divisor"] for model in models} == {1, 100_000, 1_000_000, 10_000_000}
    assert len({model["valuation"]["per_share"] for model in models}) == 1
    assert all(model["historical"][1]["revenue"] == 12_000_000_000 for model in models)


def test_overrides_are_explicit_and_share_count_is_absolute(stock_observation):
    standard = build_stock_model(stock_observation)
    altered = build_stock_model(
        stock_observation, {"shares_outstanding": 200_000_000}
    )
    assert altered["valuation"]["per_share"] == pytest.approx(
        standard["valuation"]["per_share"] / 2
    )
    assert altered["assumption_notes"]["shares_outstanding"].startswith("Explicit user")
    assert altered["source_snapshot"]["shares_outstanding"] == 100_000_000


@pytest.mark.parametrize(
    "override",
    [
        {"discount_rate": float("nan")},
        {"discount_rate": 0.03, "terminal_growth": 0.04},
        {"tax_rate": True},
        {"shares_outstanding": 0},
        {"unknown": 1},
    ],
)
def test_invalid_assumptions_fail_before_producing_a_model(stock_observation, override):
    with pytest.raises(ValueError):
        build_stock_model(stock_observation, override)


def test_missing_annual_values_stay_missing_and_never_use_sample_data(stock_observation):
    stock_observation["financial_history"]["total_debt"] = [None, None]
    model = build_stock_model(stock_observation)
    assert model["valuation"] is None and model["forecast"] == []
    assert "same annual period" in model["withheld_reason"]
    assert all(row["total_debt"] is None for row in model["historical"])


def test_latest_complete_period_is_disclosed_when_newer_period_is_partial(stock_observation):
    stock_observation["fiscal_period_ends"].append("2026-03-31")
    for key, series in stock_observation["financial_history"].items():
        series.append(2026 if key == "years" else 13_000_000_000 if key == "revenue" else None)
    model = build_stock_model(stock_observation)
    assert model["base_period"] == "2025-03-31"
    assert any("Newer partial" in warning for warning in model["warnings"])
    assert model["forecast"][0]["year"] == 2026


def test_financial_institutions_export_history_but_do_not_get_operating_dcf(stock_observation):
    stock_observation["sector"] = "Financial Services"
    model = build_stock_model(stock_observation)
    assert model["valuation"] is None and model["forecast"] == []
    assert len(model["historical"]) == 2
    assert "unsuitable" in model["withheld_reason"]


def test_nonpositive_terminal_cashflow_withholds_valuation_not_zero_target(stock_observation):
    model = build_stock_model(stock_observation, {"capex_rate": 0.9})
    assert len(model["forecast"]) == 5
    assert model["valuation"] is None
    assert "nonpositive" in model["withheld_reason"]


def test_source_snapshot_not_mutated_by_assumption_or_unit_selection(stock_observation):
    original = deepcopy(stock_observation)
    model = build_stock_model(stock_observation, {"net_debt": -100_000_000}, "million")
    model["source_snapshot"]["financial_history"]["revenue"][0] = 7
    assert stock_observation == original


def test_capm_and_after_tax_debt_cost_drive_the_active_wacc(stock_observation):
    stock_observation.update(beta=1.2, market_cap=12_000_000_000)
    stock_observation["financial_history"]["interest_expense"] = [80_000_000, 88_000_000]
    model = build_stock_model(stock_observation)
    assumptions = model["assumptions"]
    assert model["wacc"]["method"] == "capm"
    assert model["wacc"]["cost_of_equity"] == pytest.approx(0.065 + 1.2 * 0.06)
    assert assumptions["pretax_cost_of_debt"] == pytest.approx(88_000_000 / 1_100_000_000)
    assert assumptions["debt_weight"] == pytest.approx(1_100_000_000 / 13_100_000_000)
    expected = (
        model["wacc"]["cost_of_equity"] * (1 - assumptions["debt_weight"])
        + assumptions["pretax_cost_of_debt"]
        * (1 - assumptions["tax_rate"])
        * assumptions["debt_weight"]
    )
    assert assumptions["discount_rate"] == pytest.approx(expected)
    more_risk = build_stock_model(stock_observation, {"risk_free_rate": 0.08})
    assert more_risk["valuation"]["per_share"] < model["valuation"]["per_share"]
    assert more_risk["forecast"][0]["free_cash_flow"] == model["forecast"][0]["free_cash_flow"]
    manual = build_stock_model(stock_observation, {"wacc_override": 1, "discount_rate": 0.15})
    assert manual["wacc"]["active_wacc"] == 0.15 and manual["wacc"]["method"] == "manual"


def test_operating_working_capital_days_reconcile_to_fcff(stock_observation):
    history = stock_observation["financial_history"]
    history.update(
        {
            "accounts_receivable": [500_000_000, 600_000_000],
            "inventory": [700_000_000, 840_000_000],
            "accounts_payable": [300_000_000, 360_000_000],
            "cost_of_revenue": [6_000_000_000, 7_200_000_000],
        }
    )
    standard = build_stock_model(stock_observation)
    wc = standard["working_capital"]
    assert wc["method"] == "operating_days" and wc["base_observations"]
    assert wc["base_balance"] == 1_080_000_000
    assert standard["assumptions"]["receivable_days"] == pytest.approx(
        600_000_000 / 12_000_000_000 * 365
    )
    previous = wc["base_balance"]
    for row, projected in zip(wc["forecast"], standard["forecast"], strict=True):
        assert row["working_capital_balance"] == pytest.approx(
            row["receivables"] + row["inventory"] - row["payables"]
        )
        assert row["change_in_working_capital"] == pytest.approx(
            row["working_capital_balance"] - previous
        )
        assert projected["free_cash_flow"] == pytest.approx(
            projected["nopat"]
            + projected["depreciation"]
            - projected["capital_expenditure"]
            - row["change_in_working_capital"]
        )
        previous = row["working_capital_balance"]
    changed = build_stock_model(
        stock_observation, {"receivable_days": standard["assumptions"]["receivable_days"] + 5}
    )
    assert changed["forecast"][0]["free_cash_flow"] == pytest.approx(
        standard["forecast"][0]["free_cash_flow"] - standard["forecast"][0]["revenue"] * 5 / 365
    )
    assert changed["valuation"]["per_share"] < standard["valuation"]["per_share"]


def test_missing_working_capital_observations_are_not_invented(stock_observation):
    model = build_stock_model(stock_observation)
    assert model["working_capital"]["method"] == "revenue_ratio"
    assert model["working_capital"]["base_observations"] is False
    assert model["historical"][-1]["accounts_receivable"] is None
    assert model["working_capital"]["forecast"][0]["receivables"] is None
    assert "illustrative" in model["assumption_notes"]["working_capital_rate"]
    assert "assumption" in model["assumption_notes"]["risk_free_rate"]


def test_noncontrolling_interest_is_deducted_only_when_observed(stock_observation):
    baseline = build_stock_model(stock_observation)
    stock_observation["financial_history"]["noncontrolling_interest"] = [50_000_000, 60_000_000]
    model = build_stock_model(stock_observation)
    assert model["valuation"]["noncontrolling_interest"] == 60_000_000
    assert model["valuation"]["per_share"] == pytest.approx(
        baseline["valuation"]["per_share"] - 60_000_000 / 100_000_000
    )


def test_withheld_baseline_keeps_the_sensitivity_grid_available_for_recovery(stock_observation):
    model = build_stock_model(stock_observation, {"net_debt": 1_000_000_000_000})
    assert model["valuation"] is None
    assert len(model["forecast"]) == 5
    assert len(model["sensitivity"]["discount_rates"]) == 5
    assert len(model["sensitivity"]["terminal_growth_rates"]) == 5
    assert all(len(row) == 5 for row in model["sensitivity"]["values"])
    assert model["sensitivity"]["values"][2][2] is None


def test_growth_fades_and_terminal_growth_has_consistent_reinvestment(stock_observation):
    model = build_stock_model(stock_observation)
    assert [row["revenue_growth"] for row in model["forecast"]] == pytest.approx(
        [0.2, 0.1575, 0.115, 0.0725, 0.03]
    )
    terminal = model["terminal"]
    assert terminal["method"] == "roic_equals_wacc"
    assert terminal["roic"] == model["wacc"]["active_wacc"]
    assert terminal["reinvestment_rate"] == pytest.approx(terminal["growth"] / terminal["roic"])
    assert terminal["free_cash_flow"] == pytest.approx(terminal["nopat"] - terminal["reinvestment"])
    explicit = build_stock_model(stock_observation, {"terminal_roic": 0.2, "revenue_growth_end": 0.06})
    assert explicit["forecast"][-1]["revenue_growth"] == pytest.approx(0.06)
    assert explicit["terminal"]["roic"] == 0.2
    assert explicit["terminal"]["method"] == "explicit_roic"


def test_normalization_uses_three_valid_ratios_and_recent_growth_endpoints(stock_observation):
    history = stock_observation["financial_history"]
    stock_observation["fiscal_period_ends"] = ["2018-03-31", "2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31"]
    for key in list(history):
        history[key] = [None, None, None, *history[key]]
    history["years"] = [2018, 2022, 2023, 2024, 2025]
    history["revenue"] = [1_000_000, 8_000_000_000, 9_000_000_000, 10_000_000_000, 12_000_000_000]
    history["ebit"] = [1, 800_000_000, 900_000_000, 2_000_000_000, 4_800_000_000]
    history["capital_expenditure"] = [None, None, 450_000_000, -500_000_000, 3_600_000_000]
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["ebit_margin"] == pytest.approx(0.2)
    assert model["assumptions"]["capex_rate"] == pytest.approx(0.05)
    assert model["assumptions"]["revenue_growth"] == pytest.approx((12 / 8) ** (1 / 3) - 1)
    assert model["normalization"]["ebit_margin"] == ["2023-03-31", "2024-03-31", "2025-03-31"]
    assert model["normalization"]["revenue_growth"] == ["2022-03-31", "2025-03-31"]
    assert "2023-03-31" in model["assumption_notes"]["capex_rate"]


def test_first_fiscal_cash_flow_is_prorated_and_discounted_from_valuation_date(stock_observation):
    stock_observation["price_as_of"] = "2025-09-30T12:00:00+05:30"
    model = build_stock_model(stock_observation)
    first = model["forecast"][0]
    remaining_days = (date(2026, 3, 31) - date(2025, 9, 30)).days
    assert model["valuation_date"] == "2025-09-30"
    assert first["cash_flow_date"] == "2026-03-31"
    assert first["discount_period"] == pytest.approx(remaining_days / 365.25)
    assert first["valuation_cash_flow_fraction"] == pytest.approx(remaining_days / 365)
    assert first["valuation_free_cash_flow"] == pytest.approx(first["free_cash_flow"] * remaining_days / 365)
    assert first["present_value"] == pytest.approx(first["valuation_free_cash_flow"] / (1 + model["assumptions"]["discount_rate"]) ** (remaining_days / 365.25))
    assert model["forecast"][1]["valuation_cash_flow_fraction"] == 1


def test_ended_first_forecast_period_withholds_stale_valuation(stock_observation):
    stock_observation["price_as_of"] = "2026-10-27T10:00:00Z"
    model = build_stock_model(stock_observation)
    assert model["valuation"] is None
    assert "stale annual coverage" in model["withheld_reason"]
    assert len(model["historical"]) == 2


def test_missing_dates_use_fiscal_end_without_market_price_comparison(stock_observation):
    stock_observation.pop("price_as_of")
    stock_observation["retrieved_at"] = "invalid"
    model = build_stock_model(stock_observation)
    assert model["valuation_date"] == "2025-03-31"
    assert model["valuation"]["current_price"] is None
    assert model["valuation"]["upside_percent"] is None
    assert model["quality"]["status"] == "needs_review"


def test_losses_do_not_generate_assumed_cash_tax_refunds(stock_observation):
    model = build_stock_model(stock_observation, {"ebit_margin": -0.1})
    assert all(row["nopat"] == row["ebit"] for row in model["forecast"])
    assert model["valuation"] is None


def test_sensitivity_recalculates_terminal_reinvestment_for_each_rate_and_growth(stock_observation):
    for override in ({}, {"terminal_roic": 0.2}):
        model = build_stock_model(stock_observation, override)
        rate, growth = model["sensitivity"]["discount_rates"][1], model["sensitivity"]["terminal_growth_rates"][3]
        roic = override.get("terminal_roic", rate)
        last = model["forecast"][-1]
        nopat = last["ebit"] * (1 + growth) * (1 - model["assumptions"]["tax_rate"])
        terminal = nopat * (1 - growth / roic) / (rate - growth)
        pv = sum(row["valuation_free_cash_flow"] / (1 + rate) ** row["discount_period"] for row in model["forecast"])
        equity = pv + terminal / (1 + rate) ** last["discount_period"] - model["assumptions"]["net_debt"]
        assert model["sensitivity"]["values"][1][3] == pytest.approx(equity / model["assumptions"]["shares_outstanding"])


def test_dated_source_market_rates_are_used_and_disclosed(stock_observation):
    stock_observation["beta_observation"] = {
        "value": 1.2, "as_of": "2025-03-31", "benchmark": "^NSEI",
        "method": "OLS of two years of paired weekly stock and NIFTY returns",
        "observations": 104, "r_squared": 0.6, "standard_error": 0.1,
        "source_url": "https://finance.yahoo.com/quote/REAL.NS/history/", "warnings": [],
    }
    stock_observation["market_assumptions"] = {
        "risk_free_rate": {"value": 0.067, "as_of": "2025-03-31", "source_url": "https://www.rbi.org.in/"},
        "equity_risk_premium": {"value": 0.075, "as_of": "2025-01-01", "source_url": "https://pages.stern.nyu.edu/~adamodar/"},
    }
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["risk_free_rate"] == 0.067
    assert model["assumptions"]["equity_risk_premium"] == 0.075
    assert "rbi.org.in" in model["assumption_notes"]["risk_free_rate"]
    assert not any("no dated source-backed" in issue for issue in model["quality"]["issues"])
    assert model["quality"]["status"] == "needs_review"
    assert any("differs materially" in issue for issue in model["quality"]["issues"])


def test_low_beta_and_ebit_proxy_are_flagged_without_altering_observations(stock_observation):
    stock_observation["beta"] = 0.1
    stock_observation["metric_sources"] = {"ebit": ["Operating Income", "EBIT"]}
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["beta"] == 0.1
    assert model["quality"]["status"] == "needs_review"
    assert any("below 0.3" in issue for issue in model["quality"]["issues"])
    assert any("EBIT fallback" in issue for issue in model["quality"]["issues"])


def test_terminal_loss_and_over_reinvestment_cannot_create_a_positive_value(stock_observation):
    model = build_stock_model(stock_observation, {"ebit_margin": -0.1, "terminal_roic": 0.01})
    assert model["terminal"]["nopat"] < 0
    assert model["terminal"]["reinvestment_rate"] > 1
    assert model["valuation"] is None


def test_valuation_is_not_calibrated_to_the_observed_market_price(stock_observation):
    stock_observation["market_cap"] = 12_000_000_000
    baseline = build_stock_model(stock_observation)
    stock_observation["current_price"] = 500
    changed = build_stock_model(stock_observation)
    assert changed["valuation"]["per_share"] == baseline["valuation"]["per_share"]
    assert changed["valuation"]["upside_percent"] != baseline["valuation"]["upside_percent"]


def test_derived_risk_free_note_exposes_both_observation_sources(stock_observation):
    stock_observation["market_assumptions"] = {
        "risk_free_rate": {
            "value": 0.052, "as_of": "2025-03-28", "source_url": "https://www.rbi.org.in/",
            "label": "Estimated INR risk-free rate: RBI sovereign yield minus NYU rating spread",
            "basis": "Derived estimate using observations of different dates; not a quoted default-free rate",
            "adjustment_source_url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html",
            "adjustment_as_of": "2025-01-01",
        },
        "equity_risk_premium": {"value": 0.075, "as_of": "2025-01-01", "source_url": "https://pages.stern.nyu.edu/~adamodar/"},
    }
    model = build_stock_model(stock_observation)
    note = model["assumption_notes"]["risk_free_rate"]
    assert "sovereign yield minus NYU rating spread" in note
    assert "different dates" in note
    assert "rbi.org.in" in note and "ctryprem.html" in note
    assert "2025-03-28" in note and "2025-01-01" in note
    assert "annual" in model["assumption_notes"]["equity_risk_premium"]
    assert "not a live market quote" in model["assumption_notes"]["equity_risk_premium"]


@pytest.mark.parametrize("beta_value,r_squared", [(0.194, 0.2), (1.2, 0.01)])
def test_documented_beta_is_retained_with_appropriate_uncertainty_flags(stock_observation, beta_value, r_squared):
    stock_observation["beta"] = 1.0
    stock_observation["beta_observation"] = {
        "value": beta_value, "as_of": "2025-03-31", "benchmark": "^NSEI",
        "method": "OLS of two years of paired weekly stock and NIFTY returns",
        "observations": 104, "r_squared": r_squared, "standard_error": 0.1,
        "source_url": "https://finance.yahoo.com/quote/REAL.NS/history/", "warnings": [],
    }
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["beta"] == beta_value
    assert "paired observations: 104" in model["assumption_notes"]["beta"]
    assert "R squared" in model["assumption_notes"]["beta"]
    assert model["wacc"]["beta_observation"] == stock_observation["beta_observation"]
    assert model["quality"]["status"] == "needs_review"
    phrase = "below 0.3" if beta_value < 0.3 else "less than 10%"
    assert any(phrase in issue for issue in model["quality"]["issues"])


def test_published_beta_without_window_or_benchmark_remains_unverified(stock_observation):
    stock_observation["beta_observation"] = {
        "value": 1.2, "as_of": "2025-03-31",
        "method": "Yahoo published beta; benchmark/window unverified",
        "observations": None, "r_squared": None, "source_url": "https://finance.yahoo.com/quote/REAL.NS/",
        "warnings": ["Published beta benchmark is unverified"],
    }
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["beta"] == 1.2
    assert model["quality"]["status"] == "needs_review"
    assert any("confidence is unverified" in issue for issue in model["quality"]["issues"])


def test_beta_outside_supported_range_is_retained_as_source_and_uses_disclosed_assumption(stock_observation):
    stock_observation["beta_observation"] = {"value": 6.5, "method": "Unusual observed regression"}
    model = build_stock_model(stock_observation)
    assert model["source_snapshot"]["beta_observation"]["value"] == 6.5
    assert model["assumptions"]["beta"] == 1.0
    assert "outside the supported 0 to 5 range" in model["assumption_notes"]["beta"]
    assert model["quality"]["status"] == "needs_review"


def test_extreme_normalized_ratio_is_flagged_without_clipping_and_override_is_explicit(stock_observation):
    stock_observation["financial_history"]["capital_expenditure"] = [20_000_000_000, 24_000_000_000]
    model = build_stock_model(stock_observation)
    assert model["assumptions"]["capex_rate"] == 2.0
    assert any("Derived capex rate" in issue for issue in model["quality"]["issues"])
    altered = build_stock_model(stock_observation, {"capex_rate": 0.05})
    assert altered["assumptions"]["capex_rate"] == 0.05
    assert not any("Derived capex rate" in issue for issue in altered["quality"]["issues"])
    assert altered["assumption_notes"]["capex_rate"].startswith("Explicit user assumption")

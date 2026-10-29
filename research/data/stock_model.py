"""A transparent operating-company DCF built only from the requested listing.

Observations are retained in absolute reporting currency. Display units never enter
the valuation math, and per-share values always use the absolute share count.
"""

from __future__ import annotations

import copy
import math
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from research.reporting.units import resolve_unit

HISTORY_METRICS = (
    "revenue",
    "ebit",
    "net_income",
    "ebitda",
    "operating_cash_flow",
    "capital_expenditure",
    "depreciation",
    "total_debt",
    "cash_and_equivalents",
    "shareholders_equity",
    "income_tax",
    "pretax_income",
    "working_capital_cash_flow",
    "accounts_receivable",
    "inventory",
    "accounts_payable",
    "cost_of_revenue",
    "current_assets",
    "current_liabilities",
    "interest_expense",
    "noncontrolling_interest",
)
ANCHOR_METRICS = (
    "revenue",
    "ebit",
    "depreciation",
    "capital_expenditure",
    "total_debt",
    "cash_and_equivalents",
)
ASSUMPTION_BOUNDS = {
    "revenue_growth": (-0.5, 1.0),
    "revenue_growth_end": (-0.5, 1.0),
    "ebit_margin": (-0.5, 0.8),
    "tax_rate": (0.0, 0.6),
    "depreciation_rate": (0.0, 1.0),
    "capex_rate": (0.0, 1.0),
    "working_capital_rate": (-1.0, 1.0),
    "discount_rate": (0.005, 0.6),
    "terminal_growth": (0.0, 0.1),
    "terminal_roic": (0.005, 1.0),
    "net_debt": (-1e16, 1e16),
    "shares_outstanding": (1.0, 1e15),
    "risk_free_rate": (0.0, 0.3),
    "equity_risk_premium": (0.0, 0.3),
    "beta": (0.0, 5.0),
    "pretax_cost_of_debt": (0.0, 0.6),
    "debt_weight": (0.0, 1.0),
    "wacc_override": (0.0, 1.0),
    "working_capital_method": (0.0, 1.0),
    "receivable_days": (0.0, 730.0),
    "inventory_days": (0.0, 730.0),
    "payable_days": (0.0, 730.0),
    "cogs_rate": (0.0, 1.5),
}


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _history(snapshot: dict) -> list[dict]:
    history = snapshot.get("financial_history") or {}
    periods = snapshot.get("fiscal_period_ends") or []
    rows = []
    for index, raw_year in enumerate(history.get("years") or []):
        year = finite_number(raw_year)
        if year is None or int(year) != year or not 1900 <= year <= 2200:
            continue
        row = {
            "year": int(year),
            "period": str(periods[index]) if index < len(periods) else str(int(year)),
        }
        for metric in HISTORY_METRICS:
            series = history.get(metric) or []
            row[metric] = finite_number(series[index]) if index < len(series) else None
        rows.append(row)
    return sorted(rows, key=lambda row: (row["year"], row["period"]))


def _overrides(values: dict | None) -> dict:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError("Assumptions must be an object containing named numeric inputs.")
    unknown = set(values) - ASSUMPTION_BOUNDS.keys()
    if unknown:
        raise ValueError("Unknown assumptions: " + ", ".join(sorted(unknown)))
    result = {}
    for key, raw in values.items():
        value = finite_number(raw)
        lower, upper = ASSUMPTION_BOUNDS[key]
        if value is None or not lower <= value <= upper:
            raise ValueError(
                f"{key.replace('_', ' ').title()} must be a finite number between {lower:g} and {upper:g}."
            )
        result[key] = value
        if key in {"wacc_override", "working_capital_method"} and value not in (0, 1):
            raise ValueError(f"{key.replace('_', ' ').title()} must be 0 or 1.")
    return result


def _bridge(
    forecast: list[dict],
    rate: float,
    growth: float,
    net_debt: float,
    shares: float,
    noncontrolling_interest: float | None = None,
    terminal_inputs: dict | None = None,
) -> dict | None:
    if rate <= growth or rate <= 0 or shares <= 0 or not forecast:
        return None
    present_value = sum(
        row.get("valuation_free_cash_flow", row["free_cash_flow"])
        / (1 + rate) ** row.get("discount_period", index + 1)
        for index, row in enumerate(forecast)
    )
    terminal = _terminal(forecast[-1], rate, growth, terminal_inputs) if terminal_inputs else None
    if terminal and (terminal["nopat"] <= 0 or not 0 <= terminal["reinvestment_rate"] <= 1):
        return None
    terminal_cash_flow = (
        terminal["free_cash_flow"] if terminal else forecast[-1]["free_cash_flow"] * (1 + growth)
    )
    if terminal_cash_flow <= 0:
        return None
    terminal_value = terminal_cash_flow / (rate - growth)
    present_terminal = terminal_value / (1 + rate) ** forecast[-1].get(
        "discount_period", len(forecast)
    )
    enterprise = present_value + present_terminal
    equity = (
        enterprise
        - net_debt
        - (noncontrolling_interest if noncontrolling_interest is not None else 0)
    )
    if (
        not all(
            math.isfinite(value)
            for value in (present_value, terminal_value, present_terminal, enterprise, equity)
        )
        or equity <= 0
    ):
        return None
    return {
        "present_value_fcff": present_value,
        "present_value_terminal": present_terminal,
        "terminal_value": terminal_value,
        "enterprise_value": enterprise,
        "net_debt": net_debt,
        "equity_value": equity,
        "shares_outstanding": shares,
        "per_share": equity / shares,
        "noncontrolling_interest": noncontrolling_interest,
        "terminal_value_share": present_terminal / enterprise if enterprise else None,
    }


def _terminal(final: dict, rate: float, growth: float, inputs: dict) -> dict:
    roic = inputs.get("explicit_roic") or rate
    ebit = final["ebit"] * (1 + growth)
    nopat = ebit - max(ebit, 0) * inputs["tax_rate"]
    reinvestment_rate = growth / roic if roic > 0 else float("inf")
    return {
        "year": final["year"] + 1,
        "revenue": final["revenue"] * (1 + growth),
        "ebit": ebit,
        "nopat": nopat,
        "growth": growth,
        "roic": roic,
        "reinvestment_rate": reinvestment_rate,
        "reinvestment": nopat * reinvestment_rate,
        "free_cash_flow": nopat * (1 - reinvestment_rate),
        "method": "explicit_roic"
        if inputs.get("explicit_roic") is not None
        else "roic_equals_wacc",
    }


def _normal_ratio(
    historical: list[dict],
    base: dict,
    numerator: str,
    denominator: str = "revenue",
    absolute: bool = False,
) -> tuple[float | None, list[str]]:
    observations = []
    for row in historical:
        top, bottom = row.get(numerator), row.get(denominator)
        if row["year"] > base["year"] or top is None or bottom is None or bottom <= 0:
            continue
        if absolute:
            top = abs(top)
        elif numerator not in {"ebit", "working_capital_cash_flow"} and top < 0:
            continue
        ratio = top / bottom
        if numerator == "income_tax" and not 0 <= ratio <= 0.6:
            continue
        observations.append((ratio, row["period"]))
    observations = observations[-3:]
    return (
        median(value for value, _ in observations) if observations else None,
        [period for _, period in observations],
    )


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _fiscal_anniversary(base: date, step: int) -> date:
    try:
        return base.replace(year=base.year + step)
    except ValueError:
        return base.replace(year=base.year + step, day=28)


def _calibrated_drivers(
    snapshot: dict, base: dict, currency: str, historical: list[dict]
) -> tuple[dict, dict, bool, dict]:
    """Keep observed calibration separate from explicit scenario assumptions."""
    revenue = base["revenue"]
    cogs = base.get("cost_of_revenue")
    cogs_ok = cogs is not None and cogs > 0
    components = [base.get(key) for key in ("accounts_receivable", "inventory", "accounts_payable")]
    wc_observed = cogs_ok and all(value is not None and value >= 0 for value in components)
    market_cap = finite_number(snapshot.get("market_cap"))
    market_cap_method = "Current Yahoo market capitalization"
    if market_cap is None or market_cap <= 0:
        price, shares = (
            finite_number(snapshot.get("current_price")),
            finite_number(snapshot.get("shares_outstanding")),
        )
        market_cap = (
            price * shares
            if price is not None and shares is not None and price > 0 and shares > 0
            else None
        )
        market_cap_method = "Current Yahoo price multiplied by current shares"
    beta_observation = snapshot.get("beta_observation")
    beta_record = beta_observation if isinstance(beta_observation, dict) else {}
    beta = finite_number(
        beta_record.get("value") if "value" in beta_record else snapshot.get("beta")
    )
    beta_observed = beta is not None and 0 <= beta <= 5
    interest, debt = base.get("interest_expense"), base["total_debt"]
    debt_cost = abs(interest) / debt if interest is not None and debt > 0 else None
    debt_cost_observed = debt_cost is not None and 0 <= debt_cost <= 0.6
    weight_observed = market_cap is not None and market_cap > 0
    ratio = (components[0] + components[1] - components[2]) / revenue if wc_observed else None
    ratio_usable = ratio is not None and -1 <= ratio <= 1
    provenance = {}
    normalized = {}
    for key, metric, denominator, scale in (
        ("receivable_days", "accounts_receivable", "revenue", 365),
        ("inventory_days", "inventory", "cost_of_revenue", 365),
        ("payable_days", "accounts_payable", "cost_of_revenue", 365),
        ("cogs_rate", "cost_of_revenue", "revenue", 1),
    ):
        value, periods = _normal_ratio(historical, base, metric, denominator)
        if value is not None:
            normalized[key] = value * scale
            provenance[key] = periods
    wc_ratios = [
        (
            (row["accounts_receivable"] + row["inventory"] - row["accounts_payable"])
            / row["revenue"],
            row["period"],
        )
        for row in historical
        if row["year"] <= base["year"]
        and row.get("revenue") is not None
        and row["revenue"] > 0
        and all(
            row.get(key) is not None and row[key] >= 0
            for key in ("accounts_receivable", "inventory", "accounts_payable")
        )
    ][-3:]
    if wc_ratios:
        normalized["working_capital_rate"] = median(value for value, _ in wc_ratios)
        provenance["working_capital_rate"] = [period for _, period in wc_ratios]
    inputs = {
        "risk_free_rate": 0.065 if currency == "INR" else 0.04,
        "equity_risk_premium": 0.06,
        "beta": beta if beta_observed else 1.0,
        "pretax_cost_of_debt": debt_cost
        if debt_cost_observed
        else (0.085 if currency == "INR" else 0.06),
        "debt_weight": debt / (debt + market_cap) if weight_observed else 0.2,
        "wacc_override": 0.0,
        "working_capital_method": 1.0 if wc_observed else 0.0,
        "receivable_days": components[0] / revenue * 365 if wc_observed else 45.0,
        "inventory_days": components[1] / cogs * 365 if wc_observed else 60.0,
        "payable_days": components[2] / cogs * 365 if wc_observed else 45.0,
        "cogs_rate": cogs / revenue if cogs_ok else 0.7,
        "working_capital_rate": ratio if ratio_usable else 0.02,
    }
    notes = {
        "risk_free_rate": "Editable illustrative risk-free-rate assumption; not a retrieved current sovereign bond yield.",
        "equity_risk_premium": "Editable illustrative 6% equity-risk-premium assumption; not an independently sourced current market estimate.",
        "beta": "Yahoo's reported beta, used as a levered-beta proxy; period and benchmark require independent verification."
        if beta_observed
        else "Explicitly illustrative beta assumption of 1.0; the reported beta was missing or outside the supported 0 to 5 range. Review or enter a supported manual input; the source observation is retained without clipping.",
        "pretax_cost_of_debt": f"Absolute annual interest expense / closing reported debt in {base['period']}; a historical borrowing-cost proxy, not a current marginal yield."
        if debt_cost_observed
        else "Editable illustrative borrowing-cost assumption; no comparable interest/debt calibration was available.",
        "debt_weight": f"Book debt divided by book debt plus equity market value. {market_cap_method}; debt and equity observations can have different dates."
        if weight_observed
        else "Editable illustrative 20% debt capital weight; a usable current equity market value was unavailable.",
        "wacc_override": "0 selects linked CAPM/after-tax-debt WACC; 1 selects the explicit manual discount-rate input.",
        "working_capital_method": "1 selects receivable/inventory/payable-day schedules; 0 selects an explicit noncash-working-capital/revenue ratio.",
        "receivable_days": f"Reported accounts receivable / revenue × 365 in {base['period']}, held constant."
        if wc_observed
        else "Editable illustrative 45-day receivables assumption; no complete comparable operating-working-capital observations were available.",
        "inventory_days": f"Reported inventory / cost of revenue × 365 in {base['period']}, held constant."
        if wc_observed
        else "Editable illustrative 60-day inventory assumption; no complete comparable operating-working-capital observations were available.",
        "payable_days": f"Reported accounts payable / cost of revenue × 365 in {base['period']}, held constant."
        if wc_observed
        else "Editable illustrative 45-day payables assumption; no complete comparable operating-working-capital observations were available.",
        "cogs_rate": f"Reported cost of revenue / revenue in {base['period']}, held constant as a forecast assumption."
        if cogs_ok
        else "Editable illustrative 70% cost-of-revenue assumption, used only for the operating-days working-capital method; not a reported fact.",
        "working_capital_rate": f"Reported receivables + inventory − payables divided by revenue in {base['period']}; a simplified operating-working-capital proxy."
        if ratio_usable
        else "Editable illustrative 2% operating-working-capital/revenue assumption; source gaps remain blank in Historical.",
    }
    inputs.update(normalized)
    for key, periods in provenance.items():
        notes[key] = (
            f"Median reported {key.replace('_', ' ')} ratio from {', '.join(periods)}; held constant as a forecast assumption."
        )
    if beta_record and beta_observed:
        notes["beta"] = (
            f"{beta_record.get('method') or 'Reported beta estimate; method unverified'}. "
            f"Benchmark: {beta_record.get('benchmark') or 'unverified'}; "
            f"paired observations: {beta_record.get('observations') if beta_record.get('observations') is not None else 'undisclosed'}; "
            f"R squared: {beta_record.get('r_squared') if beta_record.get('r_squared') is not None else 'undisclosed'}; "
            f"standard error: {beta_record.get('standard_error') if beta_record.get('standard_error') is not None else 'undisclosed'}. "
            f"As of {beta_record.get('as_of') or 'undisclosed'}; source: {beta_record.get('source_url') or 'undisclosed'}. "
            "A historical regression or published proxy does not establish the company's forward beta."
        )
    market_assumptions = snapshot.get("market_assumptions") or {}
    for key in ("risk_free_rate", "equity_risk_premium"):
        observation = market_assumptions.get(key) or {}
        value = finite_number(observation.get("value")) if isinstance(observation, dict) else None
        if value is not None and ASSUMPTION_BOUNDS[key][0] <= value <= ASSUMPTION_BOUNDS[key][1]:
            inputs[key] = value
            notes[key] = (
                f"{observation.get('label') or key.replace('_', ' ').title()}. "
                f"Retrieved market assumption as of {observation.get('as_of') or 'undisclosed date'}; "
                f"source: {observation.get('source_url') or 'undisclosed'}. "
                "A market input estimate, not a company-reported fact."
            )
            if observation.get("basis"):
                notes[key] += " " + str(observation["basis"]) + "."
            if observation.get("adjustment_source_url"):
                notes[key] += (
                    f" Adjustment source: {observation['adjustment_source_url']}; "
                    f"adjustment observation as of {observation.get('adjustment_as_of') or 'undisclosed date'}."
                )
            if key == "equity_risk_premium":
                notes[key] += (
                    " This published annual country-risk estimate is not a live market quote."
                )
    return inputs, notes, bool(wc_observed), provenance


def _working_capital(revenue: float, cogs: float, inputs: dict) -> dict:
    if inputs["working_capital_method"] == 1:
        receivables = revenue * inputs["receivable_days"] / 365
        inventory = cogs * inputs["inventory_days"] / 365
        payables = cogs * inputs["payable_days"] / 365
        balance = receivables + inventory - payables
    else:
        receivables = inventory = payables = None
        balance = revenue * inputs["working_capital_rate"]
    return {
        "receivables": receivables,
        "inventory": inventory,
        "payables": payables,
        "working_capital_balance": balance,
    }


def build_stock_model(
    snapshot: dict, assumptions: dict | None = None, display_unit: str = "auto"
) -> dict:
    """Build a phased five-year FCFF scenario, or withhold an unsuitable DCF.

    All assumption rates are decimal fractions. ``net_debt`` and
    ``shares_outstanding`` overrides are absolute currency/share units. A missing
    observation is never filled with zero or a sample-company value.
    """
    overrides = _overrides(assumptions)
    currency = str(snapshot.get("currency") or "").upper()
    historical = _history(snapshot)
    model = {
        "model_type": "LIVE",
        "company": snapshot.get("name"),
        "ticker": snapshot.get("ticker"),
        "currency": currency,
        "unit": resolve_unit(currency, display_unit),
        "generated_at": datetime.now(UTC).isoformat(),
        "source_snapshot": _safe_json(copy.deepcopy(snapshot)),
        "historical": historical,
        "base_period": None,
        "base_year": None,
        "assumptions": {},
        "assumption_notes": {},
        "forecast": [],
        "valuation": None,
        "sensitivity": {"discount_rates": [], "terminal_growth_rates": [], "values": []},
        "warnings": list(snapshot.get("warnings") or []),
        "withheld_reason": None,
        "valuation_date": None,
        "terminal": None,
        "normalization": {},
        "quality": {"status": "needs_review", "issues": []},
    }

    def withhold(reason: str) -> dict:
        model["withheld_reason"] = reason
        model["warnings"].append(reason)
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(reason)
        return model

    if not snapshot.get("name") or not snapshot.get("ticker") or len(currency) != 3:
        return withhold(
            "A verified company identity and reporting currency are required before modeling."
        )
    if any(
        word in str(snapshot.get("sector") or "").lower()
        for word in ("financial", "bank", "insurance")
    ):
        return withhold(
            "Operating-company FCFF is unsuitable for banks, insurers, and financial institutions. Historical data remains downloadable; a financial-sector valuation model is required."
        )
    if snapshot.get("financial_currency") and snapshot["financial_currency"] != currency:
        return withhold("Statement and quote currencies differ; no currency conversion is assumed.")
    anchors = [
        row
        for row in historical
        if all(row.get(metric) is not None for metric in ANCHOR_METRICS) and row["revenue"] > 0
    ]
    if not anchors:
        return withhold(
            "DCF withheld: revenue, EBIT, depreciation, capital expenditure, debt, and cash are required in the same annual period. Missing observations remain blank."
        )
    base = anchors[-1]
    model["base_period"], model["base_year"] = base["period"], base["year"]
    base_date = _iso_date(base["period"])
    if base_date is None:
        return withhold(
            "DCF withheld: a valid fiscal-period end date is required for dated cash-flow discounting."
        )
    quote_date = _iso_date(snapshot.get("price_as_of")) or _iso_date(snapshot.get("retrieved_at"))
    valuation_date = quote_date or base_date
    model["valuation_date"] = valuation_date.isoformat()
    model["timing"] = {
        "method": "actual_365_25_year_end",
        "date_source": "quote_or_retrieval_date" if quote_date else "assumed_base_fiscal_end",
        "first_year_cash_flow": "Uniform cash generation; only the fraction after the valuation date is included.",
        "market_comparison_available": quote_date is not None,
    }
    if quote_date is None:
        message = "Quote/retrieval dates were unavailable or invalid. Valuation assumes the base fiscal-period end; comparison with the current quote is withheld."
        model["warnings"].append(message)
        model["quality"]["issues"].append(message)
    if valuation_date < base_date:
        return withhold(
            "DCF withheld: valuation date precedes the fiscal base period; later financial statements cannot be used as known historical observations."
        )
    first_forecast_date = _fiscal_anniversary(base_date, 1)
    if first_forecast_date <= valuation_date:
        return withhold(
            "DCF withheld: stale annual coverage. The first forecast fiscal period has already ended at the valuation date; obtain newer annual financial statements before valuing the company."
        )
    if base is not historical[-1]:
        model["warnings"].append(
            f"The model anchors to {base['period']}, the most recent complete comparable period. Newer partial observations remain in Historical and are not mixed into the DCF."
        )
    if base["depreciation"] < 0 or base["total_debt"] < 0 or base["cash_and_equivalents"] < 0:
        return withhold(
            "DCF withheld: depreciation, debt, or cash contains an invalid negative observation."
        )
    shares = overrides.get("shares_outstanding", finite_number(snapshot.get("shares_outstanding")))
    if shares is None or shares <= 0:
        return withhold(
            "DCF withheld: a positive actual share count or an explicitly entered share-count assumption is required."
        )
    observations = [
        row
        for row in historical
        if row["year"] <= base["year"] and row["revenue"] is not None and row["revenue"] > 0
    ]
    first = next((row for row in observations if 0 < base["year"] - row["year"] <= 3), None)
    cagr = (
        (base["revenue"] / first["revenue"]) ** (1 / (base["year"] - first["year"])) - 1
        if first
        else None
    )
    if cagr is None and "revenue_growth" not in overrides:
        return withhold(
            "DCF withheld: at least two annual revenue observations are needed to calibrate growth. Enter an explicit revenue-growth assumption to model the available base period."
        )
    if cagr is not None and not -0.5 <= cagr <= 1 and "revenue_growth" not in overrides:
        return withhold(
            "DCF withheld: recent annual revenue growth is outside the supported −50% to 100% scenario range. Review the statements and enter an explicit growth assumption; no automatic growth cap is applied."
        )
    normalized = {}
    for key, metric, denominator, absolute in (
        ("ebit_margin", "ebit", "revenue", False),
        ("tax_rate", "income_tax", "pretax_income", False),
        ("depreciation_rate", "depreciation", "revenue", False),
        ("capex_rate", "capital_expenditure", "revenue", True),
    ):
        value, periods = _normal_ratio(historical, base, metric, denominator, absolute)
        normalized[key] = value
        model["normalization"][key] = periods
    model["normalization"]["revenue_growth"] = [first["period"], base["period"]] if first else []
    observed_tax = normalized["tax_rate"]
    inputs = {
        "revenue_growth": cagr if cagr is not None else overrides["revenue_growth"],
        "ebit_margin": normalized["ebit_margin"],
        "tax_rate": observed_tax if observed_tax is not None else 0.25,
        "depreciation_rate": normalized["depreciation_rate"],
        "capex_rate": normalized["capex_rate"],
        "discount_rate": 0.12,
        "terminal_growth": 0.03,
        "net_debt": base["total_debt"] - base["cash_and_equivalents"],
        "shares_outstanding": shares,
    }
    notes = {
        "revenue_growth": f"Recent revenue CAGR between {first['period']} and {base['period']} (at most three fiscal years); first-year scenario growth, not company guidance."
        if first
        else "Explicit growth assumption; insufficient recent comparable revenue history.",
        "ebit_margin": "Median reported EBIT/revenue from the last three valid comparable annual periods; held constant as a modeling assumption.",
        "tax_rate": "Median reported tax/positive pretax income from the last three valid comparable annual periods; cash taxes apply only to positive forecast EBIT."
        if observed_tax is not None
        else "Illustrative 25% tax assumption; no valid comparable reported tax rate was available.",
        "depreciation_rate": "Median reported depreciation/revenue from the last three valid comparable annual periods; held constant.",
        "capex_rate": "Median absolute reported capex/revenue from the last three valid comparable annual periods; held constant during the explicit forecast.",
        "discount_rate": "Active WACC from the linked CAPM/after-tax-debt schedule unless explicitly switched to manual mode.",
        "terminal_growth": "Explicit illustrative 3% perpetual nominal growth scenario; review against the valuation currency's long-run economic growth.",
        "net_debt": f"Reported debt less cash in {base['period']}; fiscal balances may predate the current quote/share count.",
        "shares_outstanding": "Current Yahoo share count; may have a different observation date from annual statements.",
    }
    drivers, driver_notes, wc_observed, driver_periods = _calibrated_drivers(
        snapshot, base, currency, historical
    )
    model["normalization"].update(driver_periods)
    inputs.update(drivers)
    notes.update(driver_notes)
    # Preserve older callers that intentionally submit only a manual discount rate.
    if "discount_rate" in overrides and "wacc_override" not in overrides:
        overrides["wacc_override"] = 1.0
    for key, value in overrides.items():
        inputs[key] = value
        notes[key] = "Explicit user assumption; not a reported company observation."
    inputs.setdefault("revenue_growth_end", inputs["terminal_growth"])
    notes.setdefault(
        "revenue_growth_end",
        "Fifth-year growth equals the terminal growth scenario by default; growth fades linearly from first-year growth.",
    )
    cost_of_equity = inputs["risk_free_rate"] + inputs["beta"] * inputs["equity_risk_premium"]
    after_tax_debt_cost = inputs["pretax_cost_of_debt"] * (1 - inputs["tax_rate"])
    calculated_wacc = (
        cost_of_equity * (1 - inputs["debt_weight"]) + after_tax_debt_cost * inputs["debt_weight"]
    )
    if not inputs["wacc_override"]:
        inputs["discount_rate"] = calculated_wacc
    inputs.setdefault("terminal_roic", inputs["discount_rate"])
    notes.setdefault(
        "terminal_roic",
        "Stable return on invested capital is assumed equal to active WACC: a no-excess-return terminal scenario, not an observed company fact.",
    )
    model["wacc"] = {
        "method": "manual" if inputs["wacc_override"] else "capm",
        "inputs": {
            key: inputs[key]
            for key in (
                "risk_free_rate",
                "equity_risk_premium",
                "beta",
                "pretax_cost_of_debt",
                "debt_weight",
                "tax_rate",
            )
        },
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_debt_cost,
        "calculated_wacc": calculated_wacc,
        "active_wacc": inputs["discount_rate"],
        "beta_observation": _safe_json(copy.deepcopy(snapshot.get("beta_observation"))),
    }
    if inputs["discount_rate"] <= inputs["terminal_growth"]:
        raise ValueError("Discount rate must be greater than terminal growth.")
    if inputs["discount_rate"] <= 0:
        raise ValueError("Discount rate must be positive.")
    if any(not math.isfinite(value) for value in inputs.values()):
        return withhold("Derived financial ratios are not finite; the DCF was withheld.")
    model["assumptions"], model["assumption_notes"] = inputs, notes
    for key, periods in model["normalization"].items():
        if key not in overrides and periods and key != "revenue_growth":
            notes[key] += " Observation periods: " + ", ".join(periods) + "."
    market_assumptions = snapshot.get("market_assumptions") or {}
    for key in ("risk_free_rate", "equity_risk_premium"):
        record = market_assumptions.get(key)
        if key in overrides:
            model["quality"]["issues"].append(f"{key}: explicit user scenario assumption.")
        elif (
            not isinstance(record, dict)
            or finite_number(record.get("value")) != inputs[key]
            or not record.get("source_url")
            or not _iso_date(record.get("as_of"))
        ):
            model["quality"]["issues"].append(
                f"{key}: no dated source-backed market input; illustrative scenario assumption."
            )
    if not model["quality"]["issues"]:
        model["quality"]["status"] = "sourced_inputs"
    beta_record = snapshot.get("beta_observation")
    beta_record = beta_record if isinstance(beta_record, dict) else {}
    beta_count = finite_number(beta_record.get("observations"))
    beta_r_squared = finite_number(beta_record.get("r_squared"))
    beta_source_value = finite_number(beta_record.get("value"))
    beta_method = str(beta_record.get("method") or "")
    beta_estimator_documented = (
        beta_source_value is not None
        and 0 <= beta_source_value <= 5
        and beta_source_value == inputs["beta"]
        and beta_count is not None
        and beta_count >= 78
        and beta_r_squared is not None
        and 0 <= beta_r_squared <= 1
        and beta_record.get("benchmark") == "^NSEI"
        and bool(beta_record.get("source_url"))
        and _iso_date(beta_record.get("as_of")) is not None
        and bool(beta_method)
        and "unverified" not in beta_method.lower()
    )
    if "beta" in overrides:
        issue = "Beta is an explicit user assumption; the source estimator does not validate that override."
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
    elif not beta_estimator_documented:
        issue = "Beta estimator confidence is unverified: a dated, documented NIFTY benchmark regression with at least 78 paired observations and fit statistics was unavailable or outside the supported 0 to 5 beta range."
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
        model["warnings"].append(issue)
    elif beta_r_squared < 0.1:
        issue = "The documented beta regression explains less than 10% of stock-return variation; its forward suitability remains uncertain. The estimate is retained without adjustment."
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
        model["warnings"].append(issue)
    for warning in beta_record.get("warnings") or []:
        issue = "Beta source: " + str(warning)
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
    for key, value in inputs.items():
        if key not in overrides and key in ASSUMPTION_BOUNDS:
            lower, upper = ASSUMPTION_BOUNDS[key]
            if not lower <= value <= upper:
                issue = f"Derived {key.replace('_', ' ')} ({value:g}) is outside the supported manual-input range {lower:g} to {upper:g}; review the historical periods. The observation was not clipped."
                model["quality"]["status"] = "needs_review"
                model["quality"]["issues"].append(issue)
                model["warnings"].append(issue)
    if inputs["beta"] < 0.3:
        issue = "Reported beta is below 0.3; its benchmark, estimation period and forward suitability require independent verification. The observed value is not silently adjusted."
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
        model["warnings"].append(issue)
    ebit_sources = (snapshot.get("metric_sources") or {}).get("ebit") or []
    ebit_periods = set(model["normalization"].get("ebit_margin") or []) | {base["period"]}
    all_periods = snapshot.get("fiscal_period_ends") or []
    if any(
        index < len(ebit_sources)
        and str(period) in ebit_periods
        and str(ebit_sources[index]).strip().lower() == "ebit"
        for index, period in enumerate(all_periods)
    ):
        issue = "EBIT fallback was used in the base or normalized margin periods and may include non-operating income; reported Operating Income should be verified."
        model["quality"]["status"] = "needs_review"
        model["quality"]["issues"].append(issue)
        model["warnings"].append(issue)
    model["quality"]["issues"].append(
        "Operating forecasts, terminal ROIC and book-debt/NCI adjustments remain valuation assumptions or proxies requiring review."
    )
    model["warnings"].append(
        "Five-year forecasts and the DCF are assumption-driven scenarios, not price targets or audited financial statements."
    )
    model["warnings"].append(
        "Discounting uses fiscal year-end dates and actual days/365.25. First-year FCFF is prorated for the remaining fiscal period assuming uniform cash generation; no foreign-exchange conversion is applied."
    )
    if valuation_date > base_date:
        model["warnings"].append(
            "The first forecast year's cash flow is a remaining-period estimate under a uniform cash-generation assumption; interim results and seasonality are not inferred."
        )
    previous = base["revenue"]
    wc_base = _working_capital(previous, previous * inputs["cogs_rate"], inputs)
    if wc_observed:
        wc_base = {
            "receivables": base["accounts_receivable"],
            "inventory": base["inventory"],
            "payables": base["accounts_payable"],
            "working_capital_balance": base["accounts_receivable"]
            + base["inventory"]
            - base["accounts_payable"],
        }
    if not wc_observed:
        model["warnings"].append(
            "The opening operating-working-capital balance is a constructed modeling assumption because complete comparable receivables, inventory and payables were unavailable; missing historical observations remain blank."
        )
    previous_wc = wc_base["working_capital_balance"]
    wc_rows = []
    model["working_capital"] = {
        "method": "operating_days" if inputs["working_capital_method"] else "revenue_ratio",
        "base_balance": previous_wc,
        "base_components": {key: wc_base[key] for key in ("receivables", "inventory", "payables")},
        "base_observations": wc_observed,
        "drivers": {
            key: inputs[key]
            for key in (
                "receivable_days",
                "inventory_days",
                "payable_days",
                "cogs_rate",
                "working_capital_rate",
            )
        },
        "forecast": wc_rows,
    }
    for step in range(1, 6):
        growth = (
            inputs["revenue_growth"]
            + (inputs["revenue_growth_end"] - inputs["revenue_growth"]) * (step - 1) / 4
        )
        revenue = previous * (1 + growth)
        ebit = revenue * inputs["ebit_margin"]
        nopat = ebit - max(ebit, 0) * inputs["tax_rate"]
        depreciation = revenue * inputs["depreciation_rate"]
        capex = revenue * inputs["capex_rate"]
        cogs = revenue * inputs["cogs_rate"]
        wc = _working_capital(revenue, cogs, inputs)
        working_capital = wc["working_capital_balance"] - previous_wc
        if step == 1 and wc_observed and working_capital < -0.025 * base["revenue"]:
            issue = (
                "The first forecast assumes a material working-capital cash release as balances "
                "move to the selected operating drivers. This is a scenario effect, not a reported "
                "cash inflow; verify the opening balances, normalization and interim movements."
            )
            model["quality"]["status"] = "needs_review"
            model["quality"]["issues"].append(issue)
            model["warnings"].append(issue)
        cash_flow = nopat + depreciation - capex - working_capital
        cash_flow_date = _fiscal_anniversary(base_date, step)
        discount_period = (cash_flow_date - valuation_date).days / 365.25
        fraction = (
            (cash_flow_date - valuation_date).days / (cash_flow_date - base_date).days
            if step == 1
            else 1.0
        )
        valuation_cash_flow = cash_flow * fraction
        factor = 1 / (1 + inputs["discount_rate"]) ** discount_period
        if not all(
            math.isfinite(value)
            for value in (
                revenue,
                ebit,
                nopat,
                depreciation,
                capex,
                working_capital,
                cash_flow,
                factor,
                valuation_cash_flow * factor,
            )
        ):
            model["forecast"] = []
            return withhold(
                "DCF withheld: forecast arithmetic exceeds finite numeric limits for these observations and assumptions."
            )
        model["forecast"].append(
            {
                "year": base["year"] + step,
                "revenue_growth": growth,
                "revenue": revenue,
                "ebit": ebit,
                "nopat": nopat,
                "depreciation": depreciation,
                "capital_expenditure": capex,
                "change_in_working_capital": working_capital,
                "free_cash_flow": cash_flow,
                "discount_factor": factor,
                "present_value": valuation_cash_flow * factor,
                "cash_flow_date": cash_flow_date.isoformat(),
                "discount_period": discount_period,
                "valuation_cash_flow_fraction": fraction,
                "valuation_free_cash_flow": valuation_cash_flow,
                "cost_of_revenue": cogs,
                "working_capital_balance": wc["working_capital_balance"],
            }
        )
        wc_rows.append(
            {"year": base["year"] + step, **wc, "change_in_working_capital": working_capital}
        )
        previous = revenue
        previous_wc = wc["working_capital_balance"]
    minority = base.get("noncontrolling_interest")
    if minority is not None and minority < 0:
        minority = None
    if minority is None:
        model["warnings"].append(
            "No usable non-controlling-interest observation was available. The equity bridge omits that adjustment rather than claiming the missing source amount is zero; verify other claims and non-operating assets before relying on per-share values."
        )
    else:
        model["warnings"].append(
            "The equity bridge deducts reported book non-controlling interests as a proxy; it is not a separately estimated market value of minority claims."
        )
    terminal_inputs = {
        "tax_rate": inputs["tax_rate"],
        "explicit_roic": inputs["terminal_roic"] if "terminal_roic" in overrides else None,
    }
    model["terminal"] = _terminal(
        model["forecast"][-1], inputs["discount_rate"], inputs["terminal_growth"], terminal_inputs
    )
    valuation = _bridge(
        model["forecast"],
        inputs["discount_rate"],
        inputs["terminal_growth"],
        inputs["net_debt"],
        shares,
        minority,
        terminal_inputs,
    )
    if valuation is None:
        withhold(
            "DCF valuation withheld: terminal cash flow or equity value is nonpositive under these assumptions. Forecasts remain available; revise the assumptions rather than treating zero as a valuation."
        )
    else:
        price = finite_number(snapshot.get("current_price"))
        valuation["current_price"] = (
            price if quote_date is not None and price is not None and price > 0 else None
        )
        valuation["upside_percent"] = (
            (valuation["per_share"] / price - 1) * 100
            if quote_date is not None and price is not None and price > 0
            else None
        )
        model["valuation"] = valuation
        negative_years = sum(row["free_cash_flow"] <= 0 for row in model["forecast"])
        if negative_years >= 3 or (valuation.get("terminal_value_share") or 0) > 0.85:
            transition_note = "The model relies heavily on the terminal transition or has several nonpositive forecast cash flows. Validate the company's investment cycle, sustainable reinvestment and segment economics before interpreting this scenario."
            model["quality"]["issues"].append(transition_note)
            model["quality"]["status"] = "needs_review"
            model["warnings"].append(transition_note)
        if valuation.get("upside_percent") is not None and abs(valuation["upside_percent"]) >= 40:
            gap_note = "The scenario differs materially from the observed quote. Reconcile segment forecasts, reinvestment, non-operating assets and minority claims; the price difference is not proof of valuation accuracy or mispricing."
            model["quality"]["issues"].append(gap_note)
            model["quality"]["status"] = "needs_review"
            model["warnings"].append(gap_note)
    rate_step = min(0.01, inputs["discount_rate"] / 3)
    growth_step = (
        min(0.01, inputs["terminal_growth"] / 2) if inputs["terminal_growth"] > 0 else 0.01
    )
    rates = [inputs["discount_rate"] + offset * rate_step for offset in (-2, -1, 0, 1, 2)]
    growths = (
        [max(0, inputs["terminal_growth"] + offset * growth_step) for offset in (-2, -1, 0, 1, 2)]
        if inputs["terminal_growth"] > 0
        else [offset * growth_step for offset in range(5)]
    )
    values = []
    for rate in rates:
        row = []
        for growth in growths:
            cell = _bridge(
                model["forecast"],
                rate,
                growth,
                inputs["net_debt"],
                shares,
                minority,
                terminal_inputs,
            )
            row.append(cell["per_share"] if cell is not None else None)
        values.append(row)
    model["sensitivity"] = {
        "discount_rates": rates,
        "terminal_growth_rates": growths,
        "values": values,
    }
    return model

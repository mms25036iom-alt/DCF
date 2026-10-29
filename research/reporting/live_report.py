"""Evidence-led equity research from persisted observations and disclosed models."""

import ipaddress
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from research.reporting.units import (
    finite_number,
    format_amount,
    format_number,
    format_percent,
    resolve_unit,
)

FINANCIAL_LABELS = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "ebit": "Operating profit (EBIT)",
    "pretax_income": "Profit before tax",
    "income_tax": "Income tax",
    "net_income": "Net profit",
    "operating_cash_flow": "Cash flow from operations",
    "capital_expenditure": "Capital expenditure (cash outflow)",
    "depreciation": "Depreciation & amortisation",
    "working_capital_cash_flow": "Working-capital cash-flow movement",
    "total_debt": "Total debt",
    "cash_and_equivalents": "Cash & equivalents",
    "shareholders_equity": "Shareholders' equity",
    "cost_of_revenue": "Cost of revenue",
    "accounts_receivable": "Trade receivables",
    "inventory": "Inventories",
    "accounts_payable": "Trade payables",
    "current_assets": "Current assets",
    "current_liabilities": "Current liabilities",
    "interest_expense": "Interest expense",
    "noncontrolling_interest": "Non-controlling interests",
}
METRIC_LABELS = {
    "revenue_cagr_percent": "Revenue CAGR",
    "ebit_margin_percent": "EBIT margin",
    "return_on_end_year_equity_percent": "Return on closing equity (proxy)",
    "operating_cash_flow_less_capex": "Operating cash flow less capital expenditure",
}


def public_source_url(value: Any) -> str | None:
    """Publish public source locations without credentials, query secrets or local paths."""
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443} or host in {"localhost", "localhost.localdomain"}:
            return None
        if host.endswith((".local", ".localhost", ".internal", ".lan")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if (
                "." not in host
                or not re.fullmatch(r"[a-z0-9.-]+", host)
                or host.replace(".", "").isdigit()
            ):
                return None
        netloc = f"[{host}]" if ":" in host else host
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return None


def redact_private_locations(value: str) -> str:
    value = re.sub(
        r"(?i)\b(?:local|file|uploaded-source)://[^\s<>|)]+", "[private source]", str(value)
    )
    value = re.sub(r"(?i)\b[A-Z]:[\\/][^\s<>|]+", "[private path]", value)
    return re.sub(
        r"https?://[^\s<>\])]+",
        lambda match: public_source_url(match[0]) or "[private or invalid link]",
        value,
    )


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    return (
        redact_private_locations(str(value)).replace("|", "/").replace("\r", " ").replace("\n", " ")
    )


def _text(value: Any) -> str:
    return _cell(value).replace("<", "&lt;").replace(">", "&gt;")


def _series(history: dict, key: str, index: int) -> float | None:
    values = history.get(key, [])
    return (
        finite_number(values[index]) if isinstance(values, list) and index < len(values) else None
    )


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return (
        numerator / denominator if numerator is not None and denominator not in (None, 0) else None
    )


def _derived_metrics(history: dict) -> dict:
    years = history.get("years") or []
    result = {}
    if not years:
        return result
    observations = [
        (finite_number(year), _series(history, "revenue", index))
        for index, year in enumerate(years)
    ]
    observations = [
        (year, revenue)
        for year, revenue in observations
        if year is not None and revenue is not None and revenue > 0
    ]
    if len(observations) > 1 and observations[-1][0] > observations[0][0]:
        result["revenue_cagr_percent"] = (
            (observations[-1][1] / observations[0][1])
            ** (1 / (observations[-1][0] - observations[0][0]))
            - 1
        ) * 100
    index = len(years) - 1
    for key, numerator, denominator in (
        ("ebit_margin_percent", "ebit", "revenue"),
        ("return_on_end_year_equity_percent", "net_income", "shareholders_equity"),
    ):
        value = _ratio(_series(history, numerator, index), _series(history, denominator, index))
        if value is not None:
            result[key] = value * 100
    ocf, capex = (
        _series(history, "operating_cash_flow", index),
        _series(history, "capital_expenditure", index),
    )
    if ocf is not None and capex is not None:
        result["operating_cash_flow_less_capex"] = ocf - abs(capex)
    return result


def _table(lines: list[str], headings: list[str], rows: list[list[Any]]) -> None:
    lines.append("| " + " | ".join(_cell(value) for value in headings) + " |")
    lines.append("| " + " | ".join("---" for _ in headings) + " |")
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    lines.append("")


def _primary_snapshot(state: dict) -> dict:
    ticker = state.get("selected_ticker") or state.get("ticker")
    return next(
        (snapshot for snapshot in state.get("snapshots", []) if snapshot.get("ticker") == ticker),
        (state.get("stock_model") or {}).get("source_snapshot") or {},
    )


def _metric_summary(state: dict, unit: dict) -> str:
    parts = []
    for key, value in state.get("metrics", {}).items():
        if finite_number(value) is None:
            continue
        formatted = (
            format_percent(value)
            if key.endswith("_percent")
            else f"{format_amount(value, unit)} {unit['amount_label']}"
        )
        parts.append(f"{METRIC_LABELS.get(key, key.replace('_', ' ').title())}: {formatted}")
    return (
        "; ".join(parts)
        or "Comparable annual observations are insufficient for calculated metrics."
    )


def _investment_considerations(lines: list[str], state: dict, history: dict, unit: dict) -> None:
    lines.extend(["## Investment considerations", ""])
    metrics = state.get("metrics", {})
    cagr = finite_number(metrics.get("revenue_cagr_percent"))
    margin = finite_number(metrics.get("ebit_margin_percent"))
    cash = finite_number(metrics.get("operating_cash_flow_less_capex"))
    if cagr is not None:
        lines.append(
            f"- **Growth record:** Revenue CAGR over the available comparable history is {format_percent(cagr)}. This is a historical calculation, not a forward growth forecast."
        )
    if margin is not None:
        lines.append(
            f"- **Operating economics:** Latest comparable annual EBIT margin is {format_percent(margin)}. Margin durability requires business-segment and competitive evidence."
        )
    if cash is not None:
        lines.append(
            f"- **Cash conversion:** Latest operating cash flow less capital expenditure is {format_amount(cash, unit)} {unit['amount_label']}. This cash-flow proxy differs from the forecast unlevered FCFF used in the DCF."
        )
    if not any(value is not None for value in (cagr, margin, cash)):
        lines.append(
            "A supported operating thesis cannot be built from the currently available comparable observations."
        )
    if history.get("years"):
        index = len(history["years"]) - 1
        debt, balance = (
            _series(history, "total_debt", index),
            _series(history, "cash_and_equivalents", index),
        )
        if debt is not None and balance is not None:
            lines.append(
                f"- **Capital structure:** Latest annual net debt / (cash) is {format_amount(debt - balance, unit)} {unit['amount_label']}. Annual balance-sheet observations and the current share count can have different dates."
            )
    lines.extend(
        [
            "- **Investment stance:** NOT RATED. A model output alone is insufficient to establish a price target or a buy/sell recommendation.",
            "",
        ]
    )


def _historical_financials(lines: list[str], state: dict, history: dict, unit: dict) -> None:
    years = history.get("years", [])
    periods = state.get("fiscal_period_ends", [])
    labels = periods if len(periods) == len(years) else years
    lines.extend(
        [
            "## Historical financial performance",
            "",
            f"Financial amounts in **{unit['amount_label']}**. Source observations remain in their original currency; presentation divides amounts by {unit['divisor']:,}. Prices and per-share values are not scaled. Missing values remain N/A.",
            "",
        ]
    )
    if not years:
        lines.extend(["Comparable annual statements were not available.", ""])
        return
    lines.extend(
        [
            "Period headers are fiscal period-end dates where supplied by Yahoo. Rows use the same period column; missing values are never replaced with a different year's observation.",
            "",
        ]
    )
    ordered = [key for key in FINANCIAL_LABELS if key in history]
    ordered += [
        key
        for key, values in history.items()
        if key != "years" and key not in ordered and isinstance(values, list)
    ]
    rows = [
        [
            FINANCIAL_LABELS.get(key, key.replace("_", " ").title()),
            *[format_amount(_series(history, key, index), unit) for index in range(len(years))],
        ]
        for key in ordered
    ]
    cash_after_capex, net_debt = [], []
    for index in range(len(years)):
        ocf, capex = (
            _series(history, "operating_cash_flow", index),
            _series(history, "capital_expenditure", index),
        )
        debt, cash = (
            _series(history, "total_debt", index),
            _series(history, "cash_and_equivalents", index),
        )
        cash_after_capex.append(
            format_amount(ocf - abs(capex) if ocf is not None and capex is not None else None, unit)
        )
        net_debt.append(
            format_amount(debt - cash if debt is not None and cash is not None else None, unit)
        )
    rows.extend(
        [
            ["Operating cash flow less capex (calculated)", *cash_after_capex],
            ["Net debt / (cash) (calculated)", *net_debt],
        ]
    )
    _table(lines, ["Financial metric", *[str(label) for label in labels]], rows)
    lines.extend(
        [
            "### Operating ratios",
            "",
            "Ratios below are calculated from same-period observations. Return on closing equity is a proxy using year-end equity, rather than average equity.",
            "",
        ]
    )
    ratios = {
        "Revenue growth (YoY)": [],
        "EBITDA margin": [],
        "EBIT margin": [],
        "Net profit margin": [],
        "Return on closing equity (proxy)": [],
        "Capex / revenue": [],
    }
    for index in range(len(years)):
        revenue = _series(history, "revenue", index)
        previous = _series(history, "revenue", index - 1) if index else None
        yoy = (
            revenue / previous - 1
            if revenue is not None and previous is not None and previous > 0
            else None
        )
        ratios["Revenue growth (YoY)"].append(format_percent(yoy, ratio=True))
        for title, numerator in (
            ("EBITDA margin", "ebitda"),
            ("EBIT margin", "ebit"),
            ("Net profit margin", "net_income"),
            ("Capex / revenue", "capital_expenditure"),
        ):
            value = _series(history, numerator, index)
            ratios[title].append(
                format_percent(
                    _ratio(
                        abs(value) if title == "Capex / revenue" and value is not None else value,
                        revenue,
                    ),
                    ratio=True,
                )
            )
        ratios["Return on closing equity (proxy)"].append(
            format_percent(
                _ratio(
                    _series(history, "net_income", index),
                    _series(history, "shareholders_equity", index),
                ),
                ratio=True,
            )
        )
    _table(
        lines,
        ["Calculated ratio", *[str(label) for label in labels]],
        [[key, *values] for key, values in ratios.items()],
    )
    lines.extend(["### Key financial observations", "", _metric_summary(state, unit), ""])


def _stock_model(lines: list[str], model: dict, unit: dict, currency: str) -> None:
    lines.extend(
        [
            "## Discounted cash-flow valuation",
            "",
            "Five-year operating-company FCFF model. Historical observations anchor the model; growth, margins, reinvestment, tax, discount rates, and terminal growth are explicit assumptions. Valuation is an analytical scenario, not a verified intrinsic value or price target.",
            "",
        ]
    )
    if model.get("withheld_reason"):
        lines.extend([f"**Valuation withheld:** {_text(model['withheld_reason'])}", ""])
    quality = model.get("quality") or {}
    if quality:
        lines.extend(
            [
                f"**Input review status:** {_text(quality.get('status'))}. Sourced inputs have not been independently verified or audited.",
                "",
            ]
        )
        for issue in quality.get("issues") or []:
            lines.extend([f"- {_text(issue)}", ""])
    if model.get("valuation_date"):
        lines.extend(
            [
                f"**Valuation date:** {_text(model['valuation_date'])}. Review annual coverage, quote timing and the dated macro assumptions before interpreting the scenario.",
                "",
            ]
        )
    assumptions, notes = model.get("assumptions", {}), model.get("assumption_notes", {})
    labels = {
        "revenue_growth": "First forecast revenue growth",
        "revenue_growth_end": "Fifth forecast revenue growth",
        "ebit_margin": "EBIT margin",
        "tax_rate": "Tax rate",
        "depreciation_rate": "Depreciation / revenue",
        "capex_rate": "Capex / revenue",
        "working_capital_rate": "Operating working capital / revenue",
        "discount_rate": "Discount rate (WACC)",
        "terminal_growth": "Terminal growth",
        "terminal_roic": "Terminal return on invested capital (ROIC)",
        "net_debt": "Net debt / (cash)",
        "shares_outstanding": "Current shares outstanding",
    }
    rows = []
    for key, label in labels.items():
        value = assumptions.get(key)
        if key == "net_debt":
            display = (
                f"{format_amount(value, unit)} {unit['amount_label']}"
                if finite_number(value) is not None
                else "N/A"
            )
        elif key == "shares_outstanding":
            display = (
                f"{format_number(value, 0)} shares" if finite_number(value) is not None else "N/A"
            )
        else:
            display = format_percent(value, ratio=True)
        rows.append(
            [label, display, notes.get(key) or "Editable model assumption; verify independently."]
        )
    lines.extend(["### Assumptions and their basis", ""])
    _table(lines, ["Driver", "Value", "Basis / treatment"], rows)
    normalization = model.get("normalization") or {}
    if normalization:
        lines.extend(
            [
                "Historical operating ratios use the median of the last three valid same-period observations, or the available valid subset. Initial revenue growth uses the recent three-year window's observed CAGR endpoints; it then fades linearly to the fifth-year input. The periods used for each calibration are explicit below.",
                "",
            ]
        )
        _table(
            lines,
            ["Calibrated driver", "Financial periods used"],
            [
                [
                    labels.get(key, key.replace("_", " ").title()),
                    ", ".join(str(period) for period in periods),
                ]
                for key, periods in normalization.items()
            ],
        )
    market_inputs = (model.get("source_snapshot") or {}).get("market_assumptions") or {}
    if market_inputs:
        market_rows = []
        for key, observation in market_inputs.items():
            if not isinstance(observation, dict):
                continue
            uri = public_source_url(observation.get("source_url"))
            market_rows.append(
                [
                    key.replace("_", " ").title(),
                    format_percent(observation.get("value"), ratio=True),
                    observation.get("as_of") or "Undated",
                    f"[Source]({uri})" if uri else "Public source URL unavailable",
                ]
            )
        lines.extend(["### Dated market assumptions", ""])
        _table(lines, ["Input", "Value", "As of", "Public source"], market_rows)
    wacc = model.get("wacc") or {}
    if wacc:
        inputs = wacc.get("inputs") or {}
        lines.extend(
            [
                "### Cost of capital build",
                "",
                "The active discount rate is linked to the WACC schedule in the Excel workbook. Calibration inputs and manual overrides remain visible; an assumed risk-free rate or risk premium is not presented as a current market observation.",
                "",
            ]
        )
        _table(
            lines,
            ["WACC component", "Input / calculation", "Basis"],
            [
                [
                    "Risk-free rate",
                    format_percent(inputs.get("risk_free_rate"), ratio=True),
                    "Currency-consistent modeling input",
                ],
                [
                    "Equity risk premium",
                    format_percent(inputs.get("equity_risk_premium"), ratio=True),
                    "Modeling input; review geography and valuation date",
                ],
                [
                    "Levered beta",
                    format_number(inputs.get("beta")),
                    "Provider beta estimate or explicit model assumption; review estimation window and leverage",
                ],
                [
                    "Cost of equity",
                    format_percent(wacc.get("cost_of_equity"), ratio=True),
                    "Risk-free rate + beta × equity risk premium",
                ],
                [
                    "Pre-tax cost of debt",
                    format_percent(inputs.get("pretax_cost_of_debt"), ratio=True),
                    "Historical interest / closing book debt proxy, dated source input, or explicit assumption",
                ],
                [
                    "After-tax cost of debt",
                    format_percent(wacc.get("after_tax_cost_of_debt"), ratio=True),
                    "Pre-tax cost of debt × (1 − tax rate)",
                ],
                [
                    "Debt weight",
                    format_percent(inputs.get("debt_weight"), ratio=True),
                    "Book debt / (book debt + market equity) proxy; observation dates can differ",
                ],
                [
                    "Calculated WACC",
                    format_percent(wacc.get("calculated_wacc"), ratio=True),
                    "Cost of equity × equity weight + after-tax cost of debt × debt weight",
                ],
                [
                    "Active discount rate",
                    format_percent(wacc.get("active_wacc"), ratio=True),
                    "CAPM-derived WACC"
                    if wacc.get("method") == "capm"
                    else "Explicit discount-rate override",
                ],
            ],
        )
    working_capital = model.get("working_capital") or {}
    if working_capital:
        drivers = working_capital.get("drivers") or {}
        days = working_capital.get("method") == "operating_days"
        lines.extend(
            [
                "### Operating working-capital schedule",
                "",
                "Operating working capital excludes cash and financing debt. Investment in working capital is the change in the modeled operating balance, not the reported cash-flow statement's working-capital movement.",
                "",
            ]
        )
        if days:
            _table(
                lines,
                ["Operating driver", "Active input"],
                [
                    ["Receivable days", format_number(drivers.get("receivable_days"))],
                    ["Inventory days", format_number(drivers.get("inventory_days"))],
                    ["Payable days", format_number(drivers.get("payable_days"))],
                    [
                        "Cost of revenue / revenue",
                        format_percent(drivers.get("cogs_rate"), ratio=True),
                    ],
                ],
            )
            lines.extend(
                [
                    "Receivables = revenue × receivable days / 365; inventory and payables = cost of revenue × the corresponding days / 365. Operating balance = receivables + inventory − payables.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"The active schedule uses a revenue-linked working-capital assumption of **{format_percent(drivers.get('working_capital_rate'), ratio=True)}**. This is a disclosed modeling input when a complete operating-days schedule is unavailable; it is not a substitute reported balance.",
                    "",
                ]
            )
        wc_rows = working_capital.get("forecast") or []
        if wc_rows:
            fields = {
                "working_capital_balance": "Operating working-capital balance",
                "change_in_working_capital": "Investment / (release) in working capital",
            }
            if days:
                fields = {
                    "receivables": "Trade receivables",
                    "inventory": "Inventories",
                    "payables": "Trade payables",
                    **fields,
                }
            _table(
                lines,
                [
                    f"Working capital ({unit['amount_label']})",
                    *[f"{row.get('year', 'N/A')}E" for row in wc_rows],
                ],
                [
                    [label, *[format_amount(row.get(key), unit) for row in wc_rows]]
                    for key, label in fields.items()
                ],
            )
        lines.extend(
            [
                "The base balance uses reported same-period components."
                if working_capital.get("base_observations")
                else "The base operating balance is reconstructed from explicit scenario inputs where the source components are incomplete. It is not a reported company observation.",
                "",
            ]
        )
    forecast = model.get("forecast", [])
    if forecast:
        lines.extend(
            [
                "### Forecast financials",
                "",
                f"Forecast amounts in **{unit['amount_label']}**; E denotes an estimate. These are calculated assumptions rather than reported financials.",
                "",
            ]
        )
        fields = {
            "revenue": "Revenue",
            "cost_of_revenue": "Cost of revenue",
            "ebit": "Operating profit (EBIT)",
            "nopat": "After-tax operating profit (NOPAT)",
            "depreciation": "Add: depreciation & amortisation",
            "capital_expenditure": "Less: capital expenditure",
            "change_in_working_capital": "Less: investment in working capital",
            "free_cash_flow": "Full-year unlevered free cash flow (FCFF)",
            "valuation_free_cash_flow": "FCFF after valuation date",
            "present_value": "Present value of FCFF",
        }
        _table(
            lines,
            ["Forecast metric", *[str(row.get("year", "N/A")) + "E" for row in forecast]],
            [
                [label, *[format_amount(row.get(key), unit) for row in forecast]]
                for key, label in fields.items()
            ],
        )
        lines.extend(
            [
                "FCFF = EBIT − max(EBIT, 0) × tax rate + depreciation − capital expenditure − change in working capital. Negative EBIT receives no automatic cash-tax refund. Full-year operating cash flow is retained in the forecast. The first valuation cash flow includes only the remaining fiscal-year fraction, assuming uniform cash generation; later years use full-year FCFF. Cash flows and terminal value use fiscal year-end dates and actual days / 365.25 from the valuation date.",
                "",
            ]
        )
        _table(
            lines,
            ["Valuation timing", *[f"{row.get('year', 'N/A')}E" for row in forecast]],
            [
                [
                    "Revenue growth",
                    *[format_percent(row.get("revenue_growth"), ratio=True) for row in forecast],
                ],
                [
                    "Fiscal cash-flow date",
                    *[row.get("cash_flow_date") or "N/A" for row in forecast],
                ],
                [
                    "Discount period (actual days / 365.25)",
                    *[format_number(row.get("discount_period"), 4) for row in forecast],
                ],
                [
                    "Cash-flow fraction after valuation date",
                    *[
                        format_percent(row.get("valuation_cash_flow_fraction"), ratio=True)
                        for row in forecast
                    ],
                ],
            ],
        )
    terminal = model.get("terminal") or {}
    if terminal:
        lines.extend(
            [
                "### Terminal operating economics",
                "",
                "Terminal reinvestment supports perpetual growth: reinvestment rate = growth / ROIC; terminal FCFF = terminal NOPAT × (1 − reinvestment rate). Default terminal ROIC equals active WACC, explicitly assuming no excess returns. A manual ROIC remains fixed when WACC changes.",
                "",
            ]
        )
        _table(
            lines,
            ["Terminal driver", "Value"],
            [
                ["Terminal financial year", terminal.get("year")],
                ["Terminal revenue", format_amount(terminal.get("revenue"), unit)],
                ["Terminal EBIT", format_amount(terminal.get("ebit"), unit)],
                ["Terminal NOPAT", format_amount(terminal.get("nopat"), unit)],
                ["Terminal growth", format_percent(terminal.get("growth"), ratio=True)],
                ["Terminal ROIC", format_percent(terminal.get("roic"), ratio=True)],
                [
                    "Reinvestment rate",
                    format_percent(terminal.get("reinvestment_rate"), ratio=True),
                ],
                ["Terminal reinvestment", format_amount(terminal.get("reinvestment"), unit)],
                ["Terminal FCFF", format_amount(terminal.get("free_cash_flow"), unit)],
                ["ROIC treatment", terminal.get("method")],
            ],
        )
    valuation = model.get("valuation")
    if valuation:
        lines.extend(
            [
                "### Enterprise-to-equity value bridge",
                "",
                f"Amounts in **{unit['amount_label']}**, except shares and per-share values.",
                "",
            ]
        )
        rows = [
            [
                "PV of explicit forecast FCFF",
                format_amount(valuation.get("present_value_fcff"), unit),
            ],
            [
                "Terminal value at forecast end (undiscounted)",
                format_amount(valuation.get("terminal_value"), unit),
            ],
            ["PV of terminal value", format_amount(valuation.get("present_value_terminal"), unit)],
            [
                "Terminal value share of enterprise value",
                format_percent(valuation.get("terminal_value_share"), ratio=True),
            ],
            ["Enterprise value", format_amount(valuation.get("enterprise_value"), unit)],
            ["Less: net debt / (cash)", format_amount(valuation.get("net_debt"), unit)],
            [
                "Less: reported non-controlling interests",
                format_amount(valuation.get("noncontrolling_interest"), unit),
            ],
            ["Equity value", format_amount(valuation.get("equity_value"), unit)],
            [
                "Shares outstanding (absolute shares)",
                format_number(valuation.get("shares_outstanding"), 0),
            ],
            [
                f"Illustrative value per share ({currency}, absolute)",
                format_number(valuation.get("per_share")),
            ],
            [
                f"Last observed market price ({currency}, absolute)",
                format_number(valuation.get("current_price")),
            ],
            [
                "Scenario difference versus observed price",
                format_percent(valuation.get("upside_percent")),
            ],
        ]
        _table(lines, ["Valuation component", "Calculated amount"], rows)
        lines.extend(
            [
                "Terminal value = terminal NOPAT × (1 − terminal growth / terminal ROIC) / (active WACC − terminal growth), discounted over the final forecast's actual-day period. Enterprise value = PV of forecast FCFF + PV of terminal value. Equity value = enterprise value − net debt − reported non-controlling interests. The reported non-controlling-interest balance is a book-value proxy for the subsidiary ownership claim; its market value may differ materially.",
                "",
                "The scenario difference compares a hypothetical valuation with the last available quote. It does not imply forecast price appreciation. The quote may be delayed and may differ in date from annual balance-sheet data.",
                "",
            ]
        )
        if finite_number(valuation.get("noncontrolling_interest")) is None:
            lines.extend(
                [
                    "**Missing bridge adjustment:** Non-controlling interests were not reported in the available source period. The calculation omits that adjustment; N/A does not establish a zero balance. Reconcile subsidiary ownership and other non-operating claims before relying on the equity value.",
                    "",
                ]
            )
    elif not model.get("withheld_reason"):
        lines.extend(
            [
                "The available observations do not support a complete DCF. Missing inputs have not been replaced by sample values.",
                "",
            ]
        )
    sensitivity = model.get("sensitivity") or {}
    rates, growths, values = (
        sensitivity.get("discount_rates", []),
        sensitivity.get("terminal_growth_rates", []),
        sensitivity.get("values", []),
    )
    if rates and growths and values:
        lines.extend(
            [
                "### Sensitivity to discount rate and terminal growth",
                "",
                f"Calculated value per share in **{currency}, absolute units**. Rows vary WACC; columns vary terminal growth. Each cell recomputes terminal growth / ROIC reinvestment and terminal FCFF. Default ROIC follows each row's WACC; an explicit ROIC remains fixed. Operating forecasts and the first-year cash-flow fraction are held constant. Unsupported combinations remain N/A.",
                "",
            ]
        )
        _table(
            lines,
            ["WACC / terminal growth", *[format_percent(value, ratio=True) for value in growths]],
            [
                [
                    format_percent(rate, ratio=True),
                    *[format_number(value) for value in values[index]],
                ]
                for index, rate in enumerate(rates)
                if index < len(values)
            ],
        )
    lines.append("")


def _missing_model(lines: list[str]) -> None:
    lines.extend(
        [
            "## Discounted cash-flow valuation",
            "",
            "No supported scenario valuation was produced. A validated real-data stock model is required; values from other companies or historical demonstration models are never substituted.",
            "",
        ]
    )


def render_live_report(state: dict) -> str:
    model = state.get("stock_model") or {}
    primary = _primary_snapshot(state)
    history = state.get("financial_history") or primary.get("financial_history") or {}
    state = {
        **state,
        "financial_history": history,
        "metrics": state.get("metrics") or _derived_metrics(history),
        "fiscal_period_ends": state.get("fiscal_period_ends")
        or primary.get("fiscal_period_ends")
        or [],
        "current_price": state.get("current_price", primary.get("current_price")),
        "price_date": state.get("price_date") or primary.get("price_as_of"),
    }
    currency = (
        state.get("currency")
        or model.get("currency")
        or primary.get("currency")
        or "Unverified currency"
    )
    unit = resolve_unit(
        currency, state.get("display_unit") or model.get("unit", {}).get("key") or "auto"
    )
    model = state.get("stock_model")
    provider, outputs = state.get("model_provider", "none"), state.get("agent_outputs", {})
    analyst_note = state.get("analyst_note") or {}
    generations = {output.get("generation", "data-only") for output in outputs.values()}
    actual = (
        "data-only"
        if not generations or generations <= {"data-only"}
        else f"{provider} (see each specialist's generation label)"
    )
    if analyst_note.get("text") and analyst_note.get("provider") not in {None, "none", "data-only"}:
        provider = str(analyst_note["provider"])
        actual = f"{provider}; observed data and numerical calculations remain separate"
    generated = state.get("generated_at") or datetime.now(UTC).isoformat()
    company, ticker = (
        state.get("selected_company")
        or (model or {}).get("company")
        or primary.get("name")
        or "No company selected",
        state.get("selected_ticker")
        or state.get("ticker")
        or (model or {}).get("ticker")
        or primary.get("ticker")
        or "N/A",
    )
    periods = state.get("fiscal_period_ends", [])
    lines = [
        "# Astra Research Report",
        "",
        f"**Company:** {_text(company)}",
        f"**Ticker:** {_text(ticker)}",
        "**Data Mode:** LIVE",
        f"**Exchange:** {_text(state.get('exchange') or primary.get('exchange') or 'Not verified')}",
        f"**Currency:** {_text(currency)}",
        f"**Display units:** {_text(unit['amount_label'])}",
        f"**Generated:** {_text(generated)}",
        "**Rating:** NOT RATED",
        "",
        f"**Analysis performed:** {actual}. Selected AI provider: {_text(provider)}.",
        "",
        "## Executive view",
        "",
        f"This equity research memorandum covers **{_text(company)} ({_text(ticker)})** using observed public-market data, available annual financial statements, and the selected source documents. Financial tables use **{unit['amount_label']}**; market prices and per-share values remain in absolute currency units.",
        "",
    ]
    if state.get("abstained"):
        lines.extend(
            [
                "**Research abstained:** "
                + _text(state.get("abstain_reason") or "The available evidence is insufficient."),
                "",
            ]
        )
    else:
        lines.extend(
            [
                _metric_summary(state, unit),
                "",
                "The company remains **NOT RATED**. Source completeness, original-filing reconciliation, and forward assumptions need independent review before forming an investment conclusion.",
                "",
            ]
        )
    if actual != "data-only" and state.get("final_view"):
        lines.extend(["### Research Director's assessment", "", state["final_view"], ""])
    if state.get("prompt"):
        lines.extend(["**Research mandate:** " + _text(state["prompt"]), ""])
    valuation = (model or {}).get("valuation") or {}
    if valuation:
        lines.extend(["### Valuation at a glance", ""])
        _table(
            lines,
            ["Analytical snapshot", "Active model"],
            [
                [
                    f"Observed market price ({currency} per share)",
                    format_number(valuation.get("current_price")),
                ],
                [
                    f"DCF scenario value ({currency} per share)",
                    format_number(valuation.get("per_share")),
                ],
                [
                    "Model / observed-price difference",
                    format_percent(valuation.get("upside_percent")),
                ],
                [
                    "Active discount rate",
                    format_percent(
                        (model or {}).get("assumptions", {}).get("discount_rate"), ratio=True
                    ),
                ],
                [
                    "Terminal growth",
                    format_percent(
                        (model or {}).get("assumptions", {}).get("terminal_growth"), ratio=True
                    ),
                ],
            ],
        )
    lines.extend(["## Company and listing profile", ""])
    _table(
        lines,
        ["Coverage item", "Observed information"],
        [
            ["Company / Yahoo symbol", f"{company} / {ticker}"],
            [
                "Sector / industry",
                f"{primary.get('sector') or state.get('sector') or 'N/A'} / {primary.get('industry') or 'N/A'}",
            ],
            [
                "Exchange / reporting currency",
                f"{state.get('exchange') or primary.get('exchange') or 'N/A'} / {currency}",
            ],
            [
                f"Last available price ({currency}, absolute)",
                format_number(state.get("current_price")),
            ],
            ["Price timestamp", state.get("price_date") or "Unavailable; not guaranteed realtime"],
            [
                f"Market capitalisation ({unit['amount_label']})",
                format_amount(primary.get("market_cap"), unit),
            ],
            [
                "Current shares outstanding (absolute shares)",
                format_number(primary.get("shares_outstanding"), 0),
            ],
            [
                "Latest available fiscal period",
                periods[-1] if periods else (history.get("years") or ["N/A"])[-1],
            ],
            ["Source retrieval timestamp", primary.get("retrieved_at") or "Not supplied"],
            ["Company website", primary.get("website") or "Not supplied"],
        ],
    )
    lines.extend(["### Business overview", ""])
    if primary.get("business_summary"):
        lines.extend(
            [
                _text(
                    str(primary["business_summary"])[:1400].rsplit(". ", 1)[0] + "."
                    if len(str(primary["business_summary"])) > 1400
                    else primary["business_summary"]
                ),
                "",
                "Business description supplied by Yahoo Finance; company filings are the preferred reference for segment detail.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "A verified business description or segment-level revenue breakdown was not supplied. Consult the company's latest annual report; no generic company profile has been substituted.",
                "",
            ]
        )
    _investment_considerations(lines, state, history, unit)
    _historical_financials(lines, state, history, unit)
    if isinstance(model, dict):
        _stock_model(lines, model, unit, currency)
    else:
        _missing_model(lines)
    snapshots = state.get("snapshots", [])
    if len(snapshots) > 1 or state.get("mandate_type") == "sector":
        lines.extend(
            [
                "## Requested comparison universe",
                "",
                "The universe is limited to the explicitly requested companies or disclosed sector starting list. It is not a comprehensive market screen or investment ranking.",
                "",
            ]
        )
        rows = []
        for snapshot in snapshots:
            peer_unit = resolve_unit(snapshot.get("currency"), state.get("display_unit", "auto"))
            rows.append(
                [
                    snapshot.get("name"),
                    snapshot.get("ticker"),
                    snapshot.get("sector"),
                    f"{snapshot.get('currency') or 'N/A'} {format_number(snapshot.get('current_price'))}",
                    f"{format_amount(snapshot.get('market_cap'), peer_unit)} {peer_unit['amount_label']}",
                ]
            )
        _table(
            lines,
            [
                "Company",
                "Yahoo ticker",
                "Sector",
                "Observed price (absolute)",
                "Market capitalisation",
            ],
            rows,
        )
        lines.extend(
            [
                "No currency conversion is applied. Valuations and prices in different currencies are not directly comparable.",
                "",
            ]
        )
    lines.extend(
        [
            "## Risks and evidence gaps",
            "",
            "- **Data and reporting basis:** Aggregator financials can be incomplete, delayed, restated, or mismatched in consolidation basis. Reconcile each fiscal period to the original audited filings.",
            "- **Forecast risk:** Historical growth, margins, and reinvestment ratios do not establish future outcomes. Assess customer concentration, industry conditions, and capital intensity from company-specific disclosures.",
            "- **Valuation sensitivity:** Small changes in WACC, terminal growth, capital spending, or margins can materially change the model output. Net debt and current shares may use different observation dates.",
            "- **Accounting and governance:** No fraud finding, governance score, or assurance opinion is issued. Those conclusions require evidence beyond this data set.",
            "",
        ]
    )
    for warning in dict.fromkeys([*state.get("warnings", []), *(model or {}).get("warnings", [])]):
        lines.append("- " + _text(warning))
    lines.extend(
        [
            "",
            "## Catalysts and monitorables",
            "",
            "No dated company-specific catalyst is asserted without independently classified supporting evidence. The following are research checkpoints, rather than confirmed events or forecasts:",
            "",
            "- Obtain the latest results, annual report, auditor notes, and management discussion for the requested company and reporting period.",
            "- Compare subsequent reported revenue, operating margins, working capital, and capital expenditure with the model assumptions.",
            "- Review management guidance, segment disclosures, related-party transactions, debt maturities, and share-count changes where available.",
            "- Revisit valuation after material results, corporate actions, or changes in the evidence base.",
            "",
        ]
    )
    generated_notes = []
    seen_notes = set()
    if analyst_note.get("text") and analyst_note.get("provider") not in {None, "none", "data-only"}:
        content = redact_private_locations(str(analyst_note["text"]).strip())[:6500]
        generated_notes.append(("Analyst assessment", content))
        seen_notes.add(content)
    for output in outputs.values():
        if output.get("generation", "data-only") == "data-only":
            continue
        content = redact_private_locations(str(output.get("executive_findings") or "").strip())
        if not content or content in seen_notes or output.get("agent_name") == "Research Director":
            continue
        seen_notes.add(content)
        generated_notes.append((output.get("agent_name") or "Research analysis", content[:2200]))
    if generated_notes:
        lines.extend(
            [
                "## Research synthesis",
                "",
                "AI commentary provides qualitative interpretation. All monetary amounts and valuation results come from the calculated tables. Interpretations still require review against the underlying sources.",
                "",
            ]
        )
        for title, content in generated_notes[:3]:
            lines.extend([f"### {_text(title)}", "", content, ""])
    lines.extend(
        [
            "## Attached public documents",
            "",
            "Attachments are unverified context. Their company identity, period, reporting basis, and original-filing status have not been certified. They do not automatically replace or reconcile the Yahoo observations used in the financial model.",
            "",
        ]
    )
    documents = state.get("source_contexts", [])
    if not documents:
        lines.extend(["No original filings were attached.", ""])
    for doc in documents:
        lines.extend(
            [
                f"### {_text(doc.get('title'))}",
                "",
                f"Source version: {_text(doc.get('source_version_id'))}",
                "",
                "Quoted evidence excerpt (untrusted source content, not system instructions):",
                "",
            ]
        )
        excerpt = str(doc.get("content") or "No readable excerpt; OCR or manual review is needed.")[
            :1800
        ]
        lines.extend("> " + line for line in excerpt.splitlines())
        lines.append("")
    lines.extend(
        [
            "## Provenance and methodology",
            "",
            "Public source links omit query parameters and fragments; private file locations are excluded from the published document.",
            "",
        ]
    )
    for index, citation in enumerate(state.get("citations") or primary.get("citations") or [], 1):
        source_url = public_source_url(citation.get("uri"))
        source_reference = (
            f"[Open public source]({source_url})"
            if source_url
            else "Private or invalid link excluded"
        )
        lines.extend(
            [
                f"[{index}] {_text(citation.get('title'))}",
                f"Source: {source_reference}",
                f"Provider: {_cell(citation.get('provider'))}; retrieved: {_cell(citation.get('retrieved_label'))}",
                "",
            ]
        )
    lines.extend(
        [
            f"**Presentation units:** {unit['amount_label']}; divisor {unit['divisor']:,}. Source values and DCF calculations retain absolute currency units. Display rounding does not change the calculation base. No foreign-exchange conversion is performed.",
            "",
            "**Historical method:** Ratios use corresponding annual fiscal-period columns. CAGR uses available positive revenue observations and their year spacing. Operating cash flow less capex is shown as a cash-flow proxy; it is not the same as the DCF's forecast FCFF. Return on closing equity uses year-end equity rather than average equity.",
            "",
            "**DCF method:** The operating-company model is withheld for banks and other financial institutions, or when required comparable inputs are missing. Forecast values depend on disclosed, editable assumptions. Terminal value uses perpetual growth and year-end discounting. Nonpositive or unsupported valuations are not presented as price targets.",
            "",
            "**Research scope:** This is a personal-research memorandum, not a verified brokerage research opinion, investment recommendation, assurance opinion, or promise of future performance. The company remains NOT RATED. No synthetic company values are substituted in LIVE mode.",
            "",
            "Free Yahoo access is unofficial and may be delayed, incomplete, unavailable, or rate-limited. AI narrative is optional, can contain mistakes, and must be checked against primary evidence. A language-model API key is not required for the observed financials, calculations, or workbook export.",
        ]
    )
    return redact_private_locations("\n".join(lines))

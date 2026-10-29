"""
Discounted Cash Flow (DCF) engine for equity valuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DCFInputs:
    """Inputs for DCF calculation."""

    forecast_free_cash_flows: list[Decimal]  # Annual FCFF for forecast period
    discount_rate: Decimal  # WACC as decimal (e.g., 0.09 for 9%)
    terminal_growth_rate: Decimal  # Perpetual growth rate as decimal (e.g., 0.03 for 3%)
    net_debt: Decimal  # Net debt (debt - cash)
    shares_outstanding: Decimal  # Number of shares outstanding
    fiscal_year_end: int  # Last historical fiscal year


@dataclass
class DCFOutputs:
    """Outputs from DCF calculation."""

    present_value_fcff: Decimal  # PV of forecast free cash flows
    terminal_value: Decimal  # Terminal value (enterprise value at end of forecast)
    enterprise_value: Decimal  # EV = PV(FCFF) + PV(Terminal Value)
    equity_value: Decimal  # EV - Net Debt
    implied_share_value: Decimal  # Equity Value / Shares Outstanding
    margin_of_safety: Decimal | None  # (Implied Share Value - Current Price) / Current Price
    validation_errors: list[str]  # Any validation errors encountered


class DCFEngine:
    """
    Discounted Cash Flow engine for calculating intrinsic value.
    """

    def __init__(self, inputs: DCFInputs):
        self.inputs = inputs
        self.validation_errors = self._validate_inputs()

    def _validate_inputs(self) -> list[str]:
        """Validate DCF inputs and return list of errors."""
        errors = []
        values = [
            self.inputs.discount_rate,
            self.inputs.terminal_growth_rate,
            self.inputs.net_debt,
            self.inputs.shares_outstanding,
            *self.inputs.forecast_free_cash_flows,
        ]
        if not all(value.is_finite() for value in values):
            return ["DCF inputs must be finite numbers."]
        if self.inputs.discount_rate <= self.inputs.terminal_growth_rate:
            errors.append(
                f"Discount rate ({self.inputs.discount_rate}) must be greater than "
                f"terminal growth rate ({self.inputs.terminal_growth_rate})"
            )
        if self.inputs.terminal_growth_rate < 0:
            errors.append("Terminal growth rate cannot be negative")
        if self.inputs.discount_rate <= 0:
            errors.append("Discount rate must be positive")
        if len(self.inputs.forecast_free_cash_flows) == 0:
            errors.append("Forecast free cash flows cannot be empty")
        if any(fcf < 0 for fcf in self.inputs.forecast_free_cash_flows):
            errors.append("Forecast free cash flows cannot be negative")
        if self.inputs.shares_outstanding <= 0:
            errors.append("Shares outstanding must be positive")
        return errors

    def _present_value_factor(self, rate: Decimal, periods: int) -> Decimal:
        """Calculate present value factor for annuity due."""
        if rate == 0:
            return Decimal(periods)
        return (Decimal("1") - (Decimal("1") + rate) ** (-periods)) / rate

    def calculate(self) -> DCFOutputs:
        """
        Perform DCF calculation and return results.
        If validation errors exist, outputs will still be calculated but errors will be populated.
        """
        # If there are validation errors, we still calculate but note the errors
        errors = self.validation_errors
        if errors:
            return DCFOutputs(
                present_value_fcff=Decimal("0"),
                terminal_value=Decimal("0"),
                enterprise_value=Decimal("0"),
                equity_value=Decimal("0"),
                implied_share_value=Decimal("0"),
                margin_of_safety=None,
                validation_errors=list(errors),
            )

        # Calculate present value of forecast free cash flows
        pv_fcff = Decimal("0")
        for i, fcf in enumerate(self.inputs.forecast_free_cash_flows, start=1):
            pv_factor = Decimal("1") / ((Decimal("1") + self.inputs.discount_rate) ** i)
            pv_fcff += fcf * pv_factor

        # Calculate terminal value using Gordon growth model
        # Terminal Value = FCFF[n] * (1 + g) / (WACC - g)
        if self.inputs.forecast_free_cash_flows:
            terminal_fcf = self.inputs.forecast_free_cash_flows[-1]
            denominator = self.inputs.discount_rate - self.inputs.terminal_growth_rate
            if denominator > 0:
                terminal_value = (
                    terminal_fcf * (Decimal("1") + self.inputs.terminal_growth_rate)
                ) / denominator
                # Discount terminal value back to present
                pv_terminal = terminal_value / (
                    (Decimal("1") + self.inputs.discount_rate)
                    ** len(self.inputs.forecast_free_cash_flows)
                )
            else:
                terminal_value = Decimal("0")
                pv_terminal = Decimal("0")
        else:
            terminal_value = Decimal("0")
            pv_terminal = Decimal("0")

        # Calculate enterprise value
        enterprise_value = pv_fcff + pv_terminal

        # Calculate equity value
        equity_value = enterprise_value - self.inputs.net_debt

        # Calculate implied share value
        if self.inputs.shares_outstanding > 0:
            implied_share_value = equity_value / self.inputs.shares_outstanding
        else:
            implied_share_value = Decimal("0")

        # Margin of safety requires current market price (not provided in inputs)
        # We'll leave it as None; caller can calculate if they have current price
        margin_of_safety = None

        return DCFOutputs(
            present_value_fcff=pv_fcff,
            terminal_value=terminal_value,
            enterprise_value=enterprise_value,
            equity_value=equity_value,
            implied_share_value=implied_share_value,
            margin_of_safety=margin_of_safety,
            validation_errors=errors,
        )


def calculate_dcf(
    forecast_fcff: list[Decimal],
    discount_rate: Decimal,
    terminal_growth_rate: Decimal,
    net_debt: Decimal = Decimal("0"),
    shares_outstanding: Decimal = Decimal("1"),
    fiscal_year_end: int = 2023,
) -> DCFOutputs:
    """
    Convenience function to calculate DCF.
    """
    inputs = DCFInputs(
        forecast_free_cash_flows=forecast_fcff,
        discount_rate=discount_rate,
        terminal_growth_rate=terminal_growth_rate,
        net_debt=net_debt,
        shares_outstanding=shares_outstanding,
        fiscal_year_end=fiscal_year_end,
    )
    engine = DCFEngine(inputs)
    return engine.calculate()

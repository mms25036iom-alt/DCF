/** Editable DCF drivers. Money remains absolute in requests; display scaling is reversible. */
export const LIVE_MODEL_FIELDS = [
  { key: "revenue_growth", label: "Revenue growth", kind: "percent", min: -.5, max: 1 },
  { key: "revenue_growth_end", label: "Year-five revenue growth", kind: "percent", min: -.5, max: 1 },
  { key: "ebit_margin", label: "Operating margin", kind: "percent", min: -.5, max: .8 },
  { key: "tax_rate", label: "Tax rate", kind: "percent", min: 0, max: .6 },
  { key: "depreciation_rate", label: "Depreciation / revenue", kind: "percent", min: 0, max: 1 },
  { key: "capex_rate", label: "Capital expenditure / revenue", kind: "percent", min: 0, max: 1 },
  { key: "working_capital_method", label: "Working-capital method", kind: "mode", min: 0, max: 1 },
  { key: "working_capital_rate", label: "Noncash working capital / revenue", kind: "percent", min: -1, max: 1 },
  { key: "cogs_rate", label: "Cost of revenue / revenue", kind: "percent", min: 0, max: 1.5 },
  { key: "receivable_days", label: "Receivable days / DSO", kind: "days", min: 0, max: 730 },
  { key: "inventory_days", label: "Inventory days / DIO", kind: "days", min: 0, max: 730 },
  { key: "payable_days", label: "Payable days / DPO", kind: "days", min: 0, max: 730 },
  { key: "wacc_override", label: "Discount-rate method", kind: "mode", min: 0, max: 1 },
  { key: "risk_free_rate", label: "Risk-free rate", kind: "percent", min: 0, max: .3 },
  { key: "equity_risk_premium", label: "Equity risk premium", kind: "percent", min: 0, max: .3 },
  { key: "beta", label: "Equity beta", kind: "number", min: 0, max: 5 },
  { key: "pretax_cost_of_debt", label: "Pre-tax cost of debt", kind: "percent", min: 0, max: .6 },
  { key: "debt_weight", label: "Debt weight / (debt + equity)", kind: "percent", min: 0, max: 1 },
  { key: "discount_rate", label: "Manual discount rate / WACC", kind: "percent", min: .005, max: .6 },
  { key: "terminal_growth", label: "Terminal growth", kind: "percent", min: 0, max: .1 },
  { key: "terminal_roic", label: "Stable return on invested capital", kind: "percent", min: .005, max: 1 },
  { key: "net_debt", label: "Net debt / (cash)", kind: "money", min: -1e16, max: 1e16 },
  { key: "shares_outstanding", label: "Shares outstanding", kind: "shares", min: 1, max: 1e15 },
];

/** @param {Record<string, number | null | undefined> | null | undefined} assumptions */
export function liveModelDrafts(assumptions, divisor = 1) {
  return Object.fromEntries(LIVE_MODEL_FIELDS.map(field => {
    const value = assumptions?.[field.key];
    const display = typeof value === "number" && Number.isFinite(value)
      ? value * (field.kind === "percent" ? 100 : field.kind === "money" ? 1 / divisor : 1)
      : null;
    return [field.key, display === null ? "" : String(Number(display.toPrecision(12)))];
  }));
}

/** @param {Record<string, string>} drafts
 * @returns {{ values: Record<string, number>, errors: Record<string, string> }} */
export function parseLiveModelDrafts(drafts, divisor = 1) {
  /** @type {Record<string, number>} */
  const values = {};
  /** @type {Record<string, string>} */
  const errors = {};
  for (const field of LIVE_MODEL_FIELDS) {
    const draft = String(drafts[field.key] ?? "").trim();
    const value = Number(draft);
    if (!draft || !Number.isFinite(value)) { errors[field.key] = "Enter a finite number."; continue; }
    values[field.key] = value * (field.kind === "percent" ? .01 : field.kind === "money" ? divisor : 1);
    if (values[field.key] < field.min || values[field.key] > field.max || (field.kind === "mode" && ![0, 1].includes(value))) {
      const factor = field.kind === "percent" ? 100 : field.kind === "money" ? 1 / divisor : 1;
      errors[field.key] = field.kind === "mode" ? "Choose a supported calculation method." : `Use a value from ${(field.min * factor).toLocaleString("en-IN")} to ${(field.max * factor).toLocaleString("en-IN")}${field.kind === "percent" ? "%" : ""}.`;
    }
  }
  const calculatedWacc = (values.risk_free_rate + values.beta * values.equity_risk_premium) * (1 - values.debt_weight) + values.pretax_cost_of_debt * (1 - values.tax_rate) * values.debt_weight;
  const activeWacc = values.wacc_override === 0 ? calculatedWacc : values.discount_rate;
  if (Number.isFinite(activeWacc) && Number.isFinite(values.terminal_growth) && activeWacc <= values.terminal_growth) errors.terminal_growth = "Terminal growth must be below the active WACC.";
  if (Number.isFinite(values.terminal_roic) && Number.isFinite(values.terminal_growth) && values.terminal_roic <= values.terminal_growth) errors.terminal_roic = "Stable ROIC must exceed terminal growth to support positive terminal cash flow.";
  return { values, errors };
}

/** Keep source-calibrated drivers linked until the user actually changes them.
 * @param {Record<string, number>} current
 * @param {Record<string, number>} values
 * @param {Record<string, number> | null | undefined} previous
 * @returns {Record<string, number>} */
export function liveModelOverrides(current, values, previous = null, divisor = 1) {
  const overrides = Object.fromEntries(LIVE_MODEL_FIELDS
    .filter(field => previous && Object.hasOwn(previous, field.key) && Number.isFinite(previous[field.key]))
    .map(field => [field.key, previous[field.key]]));
  // Compare with both the exact model and the same 12-digit display round-trip.
  // This avoids treating formatting as a user override without swallowing edits.
  const displayed = parseLiveModelDrafts(liveModelDrafts(current, divisor), divisor).values;
  for (const field of LIVE_MODEL_FIELDS) {
    const value = values[field.key];
    if (!Number.isFinite(value)) continue;
    const unchanged = [current[field.key], displayed[field.key]].some(base => Number.isFinite(base)
      && (value === base || (value !== 0 && base !== 0
        && Math.abs(value - base) <= Number.EPSILON * Math.max(1, Math.abs(value), Math.abs(base)) * 4)));
    if (!unchanged) overrides[field.key] = value;
  }
  // Selecting manual mode makes the visible active WACC an explicit input even
  // if its number did not change; otherwise the API's initial manual rate wins.
  if (values.wacc_override === 1 && current.wacc_override !== 1 && Number.isFinite(values.discount_rate)) {
    overrides.discount_rate = values.discount_rate;
  }
  return overrides;
}

/** @param {unknown} value */
export function liveModelNumber(value, divisor = 1, digits = 2) {
  return typeof value === "number" && Number.isFinite(value)
    ? (value / divisor).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";
}

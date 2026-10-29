import test from "node:test";
import assert from "node:assert/strict";
import { liveModelDrafts, parseLiveModelDrafts, liveModelNumber, liveModelOverrides, LIVE_MODEL_FIELDS } from "../src/lib/live-model-inputs.mjs";

const source = Object.fromEntries(LIVE_MODEL_FIELDS.map(field => [field.key, field.kind === "shares" ? 1_234_567 : field.kind === "money" ? 250_000_000 : field.kind === "mode" ? 1 : field.kind === "days" ? 45 : field.key === "discount_rate" || field.key === "terminal_roic" ? 0.12 : field.key === "beta" ? 1 : 0.03]));

test("crore and lakh money inputs round-trip to absolute currency while shares stay absolute", () => {
  for (const divisor of [1e7, 1e5, 1e6, 1]) {
    const drafts = liveModelDrafts(source, divisor);
    assert.equal(Number(drafts.net_debt), source.net_debt / divisor);
    assert.equal(Number(drafts.shares_outstanding), source.shares_outstanding);
    const parsed = parseLiveModelDrafts(drafts, divisor);
    assert.deepEqual(parsed.errors, {});
    assert.deepEqual(parsed.values, source);
  }
});

test("rates are percentages at the input boundary and negative net cash remains negative", () => {
  const drafts = liveModelDrafts({ ...source, net_debt: -50_000_000 }, 1e7);
  assert.equal(drafts.discount_rate, "12");
  assert.equal(drafts.net_debt, "-5");
  assert.equal(parseLiveModelDrafts(drafts, 1e7).values.net_debt, -50_000_000);
});

test("blank/nonfinite inputs and terminal growth above WACC do not silently create usable assumptions", () => {
  const drafts = { ...liveModelDrafts(source), revenue_growth: "", shares_outstanding: "0", discount_rate: "3", terminal_growth: "4", tax_rate: "Infinity" };
  const parsed = parseLiveModelDrafts(drafts);
  assert.ok(parsed.errors.revenue_growth);
  assert.ok(parsed.errors.shares_outstanding);
  assert.ok(parsed.errors.tax_rate);
  assert.ok(parsed.errors.terminal_growth);
});

test("missing observations stay missing and price formatting does not apply a money-unit scale", () => {
  assert.equal(liveModelNumber(null, 1e7), "—");
  assert.equal(liveModelNumber(12_000_000_000, 1e7), "1,200.00");
  assert.equal(liveModelNumber(1201.8), "1,201.80");
});

test("linked WACC validates terminal growth against CAPM and after-tax debt cost", () => {
  const drafts = liveModelDrafts({ ...source, wacc_override: 0, risk_free_rate: 0.04, equity_risk_premium: 0.05, beta: 1.2, pretax_cost_of_debt: 0.06, debt_weight: 0.25, tax_rate: 0.25, terminal_growth: 0.03 });
  assert.deepEqual(parseLiveModelDrafts(drafts).errors, {});
  drafts.terminal_growth = "9";
  assert.ok(parseLiveModelDrafts(drafts).errors.terminal_growth);
  drafts.wacc_override = "1";
  assert.equal(parseLiveModelDrafts(drafts).errors.terminal_growth, undefined);
});

test("operating-day methods and negative revenue-ratio working capital retain their meaning", () => {
  const drafts = liveModelDrafts({ ...source, working_capital_method: 1, receivable_days: 50, inventory_days: 25, payable_days: 80, working_capital_rate: -0.08 });
  const parsed = parseLiveModelDrafts(drafts);
  assert.deepEqual(parsed.errors, {});
  assert.equal(parsed.values.receivable_days, 50);
  assert.equal(parsed.values.working_capital_rate, -0.08);
  drafts.working_capital_method = "0.5";
  assert.ok(parseLiveModelDrafts(drafts).errors.working_capital_method);
});

test("changing only growth retains all other source-calibrated workbook inputs", () => {
  const current = { ...source, wacc_override: 0 };
  const values = { ...current, revenue_growth: 0.08 };
  assert.deepEqual(liveModelOverrides(current, values), { revenue_growth: 0.08 });
});

test("subsequent edits retain earlier explicit assumptions and explicit zero values", () => {
  const previous = { revenue_growth: 0.08, working_capital_rate: 0 };
  const current = { ...source, ...previous };
  const values = { ...current, ebit_margin: 0.15, capex_rate: 0 };
  assert.deepEqual(liveModelOverrides(current, values, previous), { ...previous, ebit_margin: 0.15, capex_rate: 0 });
  assert.deepEqual(previous, { revenue_growth: 0.08, working_capital_rate: 0 });
  assert.deepEqual(liveModelOverrides({ ...source, capex_rate: 1e-20 }, { ...source, capex_rate: 0 }), { capex_rate: 0 });
});

test("display precision and unit round-trips do not freeze untouched calibrations", () => {
  const current = { ...source, revenue_growth: 0.0639405357459123, net_debt: 250_000_000.123456, beta: 1.23456789012345 };
  for (const divisor of [1, 1e5, 1e6, 1e7]) {
    const values = parseLiveModelDrafts(liveModelDrafts(current, divisor), divisor).values;
    assert.deepEqual(liveModelOverrides(current, values, null, divisor), {});
  }
  // A deliberate edit past displayed precision is still an override.
  const values = parseLiveModelDrafts(liveModelDrafts(current)).values;
  values.revenue_growth += 1e-10;
  assert.deepEqual(liveModelOverrides(current, values), { revenue_growth: values.revenue_growth });
});

test("selecting manual WACC retains the displayed active rate; returning to linked mode preserves zero", () => {
  const current = { ...source, wacc_override: 0, discount_rate: 0.0706 };
  assert.deepEqual(liveModelOverrides(current, { ...current, wacc_override: 1 }), { wacc_override: 1, discount_rate: 0.0706 });
  const manual = { ...current, wacc_override: 1 };
  assert.deepEqual(liveModelOverrides(manual, { ...manual, wacc_override: 0 }, { wacc_override: 1, discount_rate: 0.0706 }), { wacc_override: 0, discount_rate: 0.0706 });
});

# DCF methodology review — 29 October 2026

The previous implementation was internally consistent in currency units and share arithmetic, but several valuation assumptions were too generic. Matching the current market price is not the test of a correct DCF. This review improves source identification and financial consistency without selecting assumptions to force a particular price.

## Corrected issues

- **Operating earnings:** prefer Yahoo's separately identified `OperatingIncome` rather than vendor `EBIT`. Preserve the raw field label for each fiscal period. Vendor EBIT can include non-operating income; its use is explicitly flagged. The Reliance reconciliation and primary filing are recorded in [the data audit](NSE_DATA_AUDIT.md).
- **Historical calibration:** use recent comparable fiscal periods and disclosed multi-year median operating drivers. Initial revenue growth uses recent observations; it is no longer silently capped at 15%. Growth phases toward a separate year-five assumption. These calibrations are estimates of future operations, not company guidance.
- **Financing inputs:** retrieve a dated RBI roughly ten-year government-security yield and the dated NYU Stern India equity premium/default-spread dataset. The INR risk-free input is explicitly an estimate derived by subtracting the rating-based sovereign spread. Publication dates, correction notes and mixed-date limitations remain visible.
- **Beta:** estimate two-year weekly price-return beta against Nifty 50 using at least 78 paired returns, completed common trading weeks, sample statistics and explicit adjustment limitations. Retain the published vendor beta for comparison. Low fit or missing evidence triggers review instead of implying statistical certainty.
- **Terminal cash flow:** link growth to required reinvestment: `terminal FCFF = terminal NOPAT × (1 − g / ROIC)`. Default stable ROIC equals active WACC, explicitly assuming no perpetual excess returns. Manual ROIC remains editable. Sensitivity recalculates terminal reinvestment in each cell.
- **Timing:** use an explicit valuation date, actual-day discount periods and a prorated first incomplete fiscal year under a disclosed uniform-cash-generation assumption. Withhold a model whose first forecast financial year has already ended. Do not compare an unstated historical valuation date with a current quote.
- **Losses and ownership:** do not assume immediate cash tax refunds on operating losses. Keep book non-controlling interests visible as a proxy for a market-value claim. Show the complete enterprise-to-equity reconciliation.
- **Exports:** Excel and PDF use the same observation token and applied assumptions. Global and yearly assumptions remain editable; dates, terminal reinvestment and macro-input calculations are linked formulas. Unchanged calibrated inputs remain linked until explicitly overridden.

## AI and credentials

Groq is configured on the server. No credential is embedded in application source, browser code, graphs, source URLs or exports. Local `.env` is ignored by Git, Vercel upload and Graphify. A hosted deployment must configure its own server environment variables. A credential shared in chat should be rotated by its owner.

The configured Groq model was selected from the account's available model list and verified with a minimal completion. Optional report narrative receives a bounded public evidence set with the numeric valuation, terminal schedule, assumptions, quality flags and sources. AI is restricted to qualitative interpretation; all published monetary amounts and valuation results come from the deterministic calculation engine. A response containing digits or financial-quantity markers is withheld, with a visible warning and the calculated report still available. The benchmark name "Nifty 50" is an explicit exception for entity identification. This guard was added after a live response incorrectly converted absolute currency amounts into billions. Qualitative interpretations still require review; this does not certify financial accuracy. Provider quotas still apply.

## Practical interpretation

A default scenario is not a verified target price. High terminal dependence, negative forecast cash flow, weak inputs and material differences from the quote are highlighted for review. Analyst judgment is still needed for management guidance, segment economics, acquisitions, non-operating investments, future capital allocation and the fair value of minority interests.

A first-year working-capital release exceeding 2.5% of base revenue receives an explicit quality warning. Moving from the reported opening balance to normalized operating-day assumptions can manufacture a large forecast inflow unless the transition is justified. The source balance is retained and the scenario effect stays visible; the warning is a diagnostic threshold, not a valuation input or an assertion that the inflow will occur.

An operating-company FCFF model is unsuitable for banks and insurers. Those results remain withheld rather than being certified as valid. Data coverage and reporting-currency mismatches can also prevent a supported valuation. The application does not claim to have independently verified every NSE company or to provide universally accurate fair values.

## Primary methodology references

- [NYU Stern: terminal excess returns and reinvestment](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)
- [NYU Stern: valuation project assumptions and stable growth](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/project/prques.htm)
- [RBI market observations](https://www.rbi.org.in/)
- [NYU Stern current datasets](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html)
- [Groq GPT-OSS model documentation](https://console.groq.com/docs/model/openai/gpt-oss-120b)

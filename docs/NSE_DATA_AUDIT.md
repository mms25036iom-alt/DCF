# NSE financial-data audit

Audit date: 29 October 2026. Public evidence only; no account credentials were accessed. The reviewed runtime snapshot identifies Reliance Industries Limited, RELIANCE.NS, INR, fiscal year ended 31 March 2026.

## Material findings

- **Operating earnings:** the reviewed Yahoo EBIT is ₹147,218 crore, equal to pretax income plus Yahoo interest expense. An operating EBIT reconciliation from the company's consolidated statement is ₹123,162 crore pretax profit + ₹27,061 crore finance costs − ₹28,962 crore other income = ₹121,261 crore. Prefer a separately identified operating-income row for FCFF. Preserve the original vendor EBIT for audit and label any fallback as a proxy; do not silently call it operating profit.
- **Cash and debt definitions:** Yahoo cash of ₹137,327 crore reconciles to reported cash of ₹145,977 crore less ₹8,466 crore collateral deposits and ₹184 crore unclaimed dividends. Yahoo debt of ₹398,000 crore reconciles to ₹374,421 crore borrowings plus ₹23,579 crore leases. These are standardized definitions, not unexplained errors.
- **Ownership:** ₹181,836 crore non-controlling interest and 13,532,472,634 issued shares match the annual filing. Book NCI is an equity-bridge proxy; shares need their own observation-date label.

Source: [Reliance FY2025–26 consolidated statements](https://www.ril.com/ar2025-26/pdf/07_RIL_IAR_2025-26_Consolidated_22-05-26.pdf), printed pages 197–199, 217 and 219.

## Provider contract and remaining limits

Exact fiscal-end dates are aligned before annual amounts are combined; unavailable rows stay missing. Retain this behavior. A union of statement periods can include an entirely empty oldest row; distinguish this from an additional observed year. Persist the statement kind, selected raw row label, fiscal end, reporting currency and secondary-source URL alongside each historical metric. Yahoo's standardized revenue and accounting definitions should not be presented as verbatim filing labels.

Cash capital expenditure and business acquisitions are different fields. Keep acquisitions separately disclosed when available and state whether a reinvestment scenario includes them; neither silently omit material acquisitions nor automatically recur a one-off acquisition every forecast year. Verify disposal sign conventions before constructing net investment. Unknown acquisition coverage must stay unknown.

An annual-only feed does not establish that the latest quarterly results were checked. Disclose the latest available annual date and whether a newer partial period was returned. Withhold a valuation if its complete annual anchor is too old, required statements are missing, periods are not comparable full years, or the security/reporting basis cannot be established. Do not join different statement-end dates merely because their year number matches.

Yahoo beta is a secondary-source proxy. Its benchmark, estimation window and observation date are not established by the `beta` number alone. A low observed beta is not a reason to invent a replacement or force a discount-rate floor. Missing or unsuitable beta should require an explicit assumption. Annual interest divided by closing book debt estimates a historical borrowing cost; it does not establish today's marginal cost of debt. Book debt combined with current equity market capitalization also mixes measurement bases and dates.

## Free, dated market inputs

The keyless adapter in `research/providers/market_assumptions.py` fetches three public resources at most, disables environment proxies and redirects, restricts HTTPS hosts, bounds downloads and XLSX expansion, and caches successful public observations for 24 hours.

1. [RBI current rates](https://www.rbi.org.in/) publishes the 6.94% GS 2036 **yield of 7.4389%**, as of **28 October 2026**. Read the yield cell, not the coupon in the security name. Select a maturity near ten years (8–12 years), preserve the security name and reject ambiguous or stale dates. Remove HTML comments before parsing: the page retains a superseded date inside a comment.
2. [NYU Stern current-data index](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) links its dated country-premium updates. Discover the latest past dated XLSX link instead of fixing a particular filename forever.
3. [The July 2026 workbook](https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx), linked as **1 July 2026**, gives India rating-based total ERP **6.9201825339%** and rating-based default spread **1.7498812238%**. Select `Total Equity Risk Premium` exactly; the similarly named second column is a different CDS-based method. The internal workbook update cell is inconsistent with the index, so the adapter discloses the index publication-date basis and warns about that discrepancy.

The derived INR default-free **estimate** is 7.4389% − 1.7498812238% = **5.6890187762%**. This combines two dated observations; it is not a directly quoted default-free instrument. Both components and their dates remain visible. India total ERP is a country proxy; a multinational's geographic exposure can require a different premium.

If the spread is unavailable, a current sovereign yield may be returned only with an explicit **unadjusted sovereign-yield proxy** label. No missing ERP or yield is replaced by an invented constant. RBI observations older than 10 days and NYU publications older than 450 days are rejected. Source failures return null observations and sanitized warnings so the valuation layer can request explicit assumptions.

The undated NYU `ctrprem.htm` table and January `ctryprem.html` table do not represent the same update as the July workbook. Do not substitute their India numbers without dates. FBIL's site exposes government-security archives but its homepage is not a dependable static G-sec yield response; RBI is the verified bounded automatic route used here. [CCIL indicative yields](https://www.ccilindia.com/web/ccil/tenorwise-indicative-yields) also state a restriction on commercial use, so they are not an interchangeable unrestricted fallback.

Validation: 15 offline adapter tests passed; Ruff passed. One live official-source adapter run completed in 7.54 seconds and reproduced the dated values above. Source retrieval does not certify the valuation's assumptions or beta methodology.

# Astra Research

A private equity-research workbench using actual Yahoo Finance observations. Enter a listing, review historical financials, change disclosed assumptions, and download a formula-linked Excel DCF and a multi-page research PDF. There is no demo provider or preloaded company portfolio.

## Run locally on Windows

Install Python 3.11 or later, uv, and Node.js 20.9 or later. From this directory:

```powershell
uv sync --locked
cd apps/web
npm ci
cd ../..
./run.ps1
```

Open http://127.0.0.1:3000/models. The launcher generates ignored local service credentials and starts only Python and Next.js, bound to loopback. PostgreSQL, Redis, Celery, and paid market-data subscriptions are not required. Use `./run.ps1 -Stop` to stop the recorded processes, `-Restart` to restart, or `-Development` for development mode. After changing frontend code, stop the app and run `npm run build` in `apps/web` before restarting production mode.

## Real data and keys

- Yahoo/yfinance provides company identity, annual financial statements and market observations without a market-data API key. Unofficial free access has no availability guarantee and may be delayed or rate limited.
- Missing observations remain unavailable. A different company or invented financial observation is never substituted. Banks/insurers and incomplete statements do not receive an unsupported operating-company DCF.
- Forecast growth, margins, risk-free rate, equity premium and other future drivers are visibly identified as assumptions. They are not claimed to be reported company facts or live market rates.
- Data-only Excel and PDF need no AI key. Optional Groq narrative uses your server-side `GROQ_API_KEY` and explicit per-report consent; local Ollama needs no key. No paid provider is provisioned by this application.
- Hosting needs generated application credentials for owner login, signed sessions, the private API and encrypted snapshots. These are your own random secrets, not purchased data keys.

Use a Yahoo listing suffix such as `RELIANCE.NS`, `TCS.NS`, or `500325.BO`. A bare symbol is interpreted as NSE. USD and other reporting currencies are retained; presentation units do not perform currency conversion.

## Downloads

The nine-sheet workbook contains Summary, Assumptions, Historical, OperatingForecast, WorkingCapital, WACC, DCF, Sensitivity and Sources. Formulas link the financial schedules, with editable drivers and source observations. Global controls appear first on Assumptions; the five-year grid below them lets you override an individual forecast year. Excel recalculates on opening; baseline cached values support previews. Monetary values use crore/lakh/million/full units, while shares and per-share prices retain absolute units. Forecasts are five years and use year-end discounting and perpetual terminal growth.

The PDF includes dated company information, investment considerations grounded in the available data, historical performance, assumptions, WACC, working capital, FCFF, the valuation bridge, sensitivity, risks, evidence gaps and source records. It uses an original layout informed by public research references. It is not affiliated with any investment bank and does not invent broker ratings or unsupported catalysts.

Optional documents must be HTTPS links on the company's source-provided website or SEC/NSE/BSE. Retrieval is limited to three links, 1 MiB each, 30 PDF pages and a 20,000-character excerpt; larger annual reports require a smaller official disclosure or manual review. Attached context does not silently replace model observations.

## Hosting and security

See [deployment instructions](docs/DEPLOYMENT.md), [security controls and limits](docs/SECURITY.md), [audit evidence](docs/SECURITY_AUDIT.md), and [research methodology](docs/RESEARCH_METHODOLOGY.md). The supplied `vercel.json` uses Vercel Services for a public Next.js frontend and private Python service. No live Vercel deployment has been performed.

The [DCF review](docs/DCF_REVIEW.md) documents the operating-income mapping, dated RBI/NYU cost-of-capital inputs, estimated Nifty beta, forecast normalization, valuation-date discounting and terminal reinvestment. Default scenarios require analyst review; they are not certified fair values for every NSE company. Optional Groq commentary is qualitative, with generated numerical claims withheld.

The hosted application is an owner-password-protected personal workspace. Do not turn it into an anonymous multi-user service without adding independent accounts, a durable distributed abuse limit and an appropriate data licence. No software can be certified security-proof by these checks.

## Verification

```powershell
./test.ps1 -Build
# Against the running local app, using real Yahoo data:
.venv/Scripts/python.exe scripts/smoke_live.py --ticker RELIANCE.NS
```

Unit tests use controlled observations to verify mathematics, failure handling and security. They are not production data. Download verification outputs stay in the ignored `output/` directory. The source recovery archive from the cleanup stays under ignored `.private/backups/`; existing private research records are not exposed by the new application.

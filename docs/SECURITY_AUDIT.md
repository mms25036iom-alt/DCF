# Security review — 28 October 2026

This document records source review and executed checks for the private, real-data research application. It is not a penetration-test certificate or a claim that the application is security-proof. Hosting configuration, future changes, provider behavior, operating-system libraries, and undisclosed vulnerabilities remain outside these checks.

## Scope and design reviewed

The reviewed deployment has an authenticated Next.js application forwarding only the four supported research endpoints to a private Python service. The Python service requires a separate server-only bearer credential and an owner identifier. Existing observations are carried in authenticated, encrypted, expiring snapshot tokens bound to the owner and ticker. Deployment must expose the new serverless application only; the historical upload, settings, database, and report-by-UUID API must not be published alongside it.

Read-only source review covered owner sessions, cookie options, origin checks, the fixed backend destination, endpoint allowlisting, request limits, error responses, snapshot handling, provider requests, official document retrieval, report generation, and export links. Configuration and secret values were not printed or copied into this document.

The official-document retriever accepts at most three HTTPS links on the observed company website or SEC, NSE, and BSE domains. It validates every DNS answer and redirect, rejects private or reserved addresses, and connects to the validated IP while retaining normal TLS certificate and hostname verification. It does not use environment proxies. Limits are 1 MiB per response, three redirects, a ten-second network deadline, thirty PDF pages, bounded PDF decompression, and twenty thousand extracted characters. Parser and network exception details are replaced with public messages. Downloaded content is kept in memory rather than published through a file-storage endpoint.

Reports treat retrieved source text and optional AI interpretations as untrusted evidence. HTML scripts and embedded elements are removed during extraction. PDF generation uses text and tables without fetching remote images or executing source HTML. Published links remove query parameters and fragments; local-file locations and credential-bearing URLs are redacted. Spreadsheet source text is written as literal text rather than executable formulas.

## Executed checks

| Check | Result |
| --- | --- |
| High-confidence provider-token, private-key, and public-environment-secret patterns in 165 source files | No matches. Ignored local secrets, generated artifacts, virtual environments, and dependencies were excluded. This does not establish that every secret format or Git-history leak was detected. |
| Focused official-source, equity-report, and PDF tests | 41 passed, including a rerun after the PDF/TLS dependency upgrades. |
| Ruff on official-source retrieval, report/PDF modules, and their focused tests | Passed. |
| Combined retained Python test suite, executed by the coordinating agent | Final rerun: 119 passed; one Starlette/httpx deprecation warning. Ruff over the retained Python sources and tests also passed. |
| Independent workbook calculation and visual verification | 35 numeric comparisons passed, including the full sensitivity grid; zero formula errors. Global growth and a single later-year margin edit propagated through valuation while historical observations stayed unchanged. All nine sheets rendered and reviewed. |
| Local production-mode owner-access integration | Sign-in/out, anonymous API denial, Origin checks, endpoint allowlisting, hosted HTTPS fail-closed behavior and nonce CSP passed using temporary test credentials. |
| Production npm advisory audit | Final rerun passed with exit code 0: zero known vulnerabilities; registry metadata lists 17 production dependencies. |
| Python production lockfile advisory audit | Final rerun passed: 51 evaluated packages, zero known vulnerabilities, zero skipped packages. |

The focused test command was:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_official_sources.py tests/test_equity_report.py tests/test_pdf_report.py
```

The retrieval tests exercise rejected URL forms before DNS lookup, mixed public/private DNS answers, pinned destination with original TLS hostname, redirect revalidation, company-domain boundaries, encoded and oversized responses, bounded HTML extraction, PDF page limits, and sanitized error content. Report tests cover observed financials, missing observations, units, per-share values, WACC and working-capital disclosure, no legacy fallback values, source redaction, and safe PDF links.

## Dependency findings and remediation status

Audits used the npm registry advisory service and `pip-audit` with PyPI advisory data. They queried package names and versions rather than application data or credentials. Initial sandbox network/cache failures were retried with network access; the results below are completed advisory responses, not inferred from a failed connection.

`npm audit --omit=dev --json`, run in `apps/web`, initially reported five affected packages: `body-parser` (high), `fast-uri` (moderate), and `deepmerge`, `@shadcn/registry`, and `shadcn` (low). The registry/CLI packages inherit the `deepmerge` finding; these are affected-package counts rather than five independent vulnerabilities. Upgrade or remove the unused CLI dependency chain and regenerate the lockfile before relying on a clean result.

- `body-parser`: upgrade to 2.4.0 or later; compressed-request denial of service, [GHSA-27r3-jxmh-p828](https://github.com/advisories/GHSA-27r3-jxmh-p828).
- `fast-uri`: upgrade to 3.1.9 or later; URL normalization and scheme/host confusion, including [GHSA-w74x-v6f9-j3xg](https://github.com/advisories/GHSA-w74x-v6f9-j3xg).
- `deepmerge`: prototype poisoning, [GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4). Removing the unused `shadcn` CLI from production removes its registry dependency chain.

Python requirements were exported from the committed production lockfile without changing it:

```powershell
uv export --frozen --no-dev --format requirements-txt --no-hashes --output-file .private/security-audit-requirements.txt
uvx --from pip-audit pip-audit --disable-pip --no-deps -r .private/security-audit-requirements.txt --format json
```

The audit initially returned nine advisory entries across two packages. One `certifi` advisory appeared twice with different fixed-version entries. Requirements are pinned, and the production dependency closure is included; environment markers select the current Python/OS combination. Development dependencies and alternative operating-system branches are not represented as audited installations.

- `certifi==2026.7.22`: upgrade to 2026.10.19 or later. The affected CA bundle includes a compromised trust chain, [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2).
- `pypdf==6.20.1`: upgrade to 6.21.2 or later. Seven findings cover malformed-PDF resource exhaustion and image-parser selection. Relevant fixes are in the primary releases [6.21.1](https://github.com/py-pdf/pypdf/releases/tag/6.21.1) and [6.21.2](https://github.com/py-pdf/pypdf/releases/tag/6.21.2). Retrieval limits reduce exposure but do not replace these fixes.

The Python owner updated the manifest, production lockfile, mirrored requirements, and local environment to `certifi==2026.10.19` and `pypdf==6.21.2`. A fresh production export and `pip-audit` rerun completed with exit code 0: **51 packages evaluated, zero known vulnerabilities, and zero skipped packages**. The 41 source/report/PDF tests passed again with the updated parser.

The frontend owner removed unused CLI, component, and Markdown dependency chains and regenerated `apps/web/package-lock.json`. A fresh `npm audit --omit=dev --json` then completed with exit code 0: **zero known vulnerabilities**. Registry metadata lists 17 production dependencies, compared with 495 before cleanup. This is the production audit result; the development dependency tree was omitted from vulnerability evaluation.

## Practical limits and deployment responsibilities

Owner-password authentication is a single private workspace, not a multi-user identity or authorization system. Browser sessions are time-limited signed cookies; clearing a cookie does not revoke a stolen copy before expiry. Rotate the session secret, access password, internal credential, and snapshot key after suspected compromise. A serverless in-memory login throttle is not a durable, distributed abuse-control service; platform-level protection and a strong randomly generated password remain relevant.

DNS validation and pinning apply to the optional official-source retriever. They are not a claim that every outbound request made by Yahoo Finance libraries is controlled by the same allowlist. The standard operating-system DNS resolver can outlive a socket deadline; backend worker limits and request timeouts bound concurrent tasks but cannot forcibly terminate a Python thread. A hostile document parser still executes within the application's process; byte, page, and decompression limits and patched dependencies are required defenses rather than a sandbox.

No automated scan establishes that all secrets, logic errors, supply-chain attacks, or data leaks are absent. This review did not test a live Vercel deployment, cloud permissions, domain takeover, TLS termination, firewall rules, backups, logs, historical Git objects, or an authenticated external penetration test. No production deployment was performed by this review. Rerun dependency audits and application checks when dependencies or public routes change.

## Groq and DCF follow-up — 29 October 2026

- The supplied Groq credential is stored only in ignored local server configuration. It is excluded from Git, deployment uploads, Graphify input and browser configuration. Hosted deployment needs its own server environment value; no secret was put in a `NEXT_PUBLIC_` variable. A credential posted in chat should be rotated by its owner.
- A final exact-credential scan of 129 checked public source, build and graph files found zero matches. Extracted PDF content and decompressed workbook parts also contained no matching live credential. This is evidence for the files checked, not an audit of chat storage or historical Git objects.
- Real Groq generation succeeded through the running application. The downloaded report had 15 rendered and visually reviewed pages; the workbook had nine sheets. API output and workbook summary values matched. Independent spreadsheet recalculation had already passed 35 comparisons across base and edited scenarios, with zero formula errors.
- Generated numerical claims are withheld from optional AI commentary. A validation failure leaves a visible warning and a usable calculated report. Provider errors use controlled messages; prompts contain bounded public research evidence rather than settings, credentials or encrypted snapshot tokens.
- Full backend regression: 188 tests passed, with one upstream Starlette/httpx deprecation warning. Frontend tests, lint, type checking and the production build passed. The production HTTP boundary check passed sign-in/out, anonymous denial, CSRF, route allowlisting, hosted-backend HTTPS fail-closed behavior and nonce CSP checks.
- Local services are bound to loopback. No Vercel deployment or external penetration test was performed. Valuation methodology and coverage limits are recorded in [the DCF review](DCF_REVIEW.md).

## GitHub publication preparation — 29 October 2026

The project source was copied into an isolated repository checkout using an explicit allowlist. The selected 96 source/configuration/documentation files were checked for the known local credential values, common provider-token/private-key signatures and personal local paths, with zero matches. Only empty credential placeholders in `.env.example` templates are included. Private environments, credentials, keys, local assistant settings, exports, research caches, logs, database files, installed dependencies and build outputs are excluded by the publication scope and strengthened ignore rules. The previously requested public research PDF remains in `reports/` and is excluded from deployment uploads.

The copied project passed 188 backend tests and 20 frontend/security tests. Vercel configuration was checked against current official Services documentation and schema; repository-root import and Framework Preset Services are required. The frontend README now agrees with the deployment guide. Test temporary directories are created on a fresh checkout. This is a file-scope publication check, not a guarantee against every undiscovered vulnerability; server credentials must be supplied privately through Vercel environment variables.

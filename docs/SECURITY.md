# Security controls and boundaries

This is a personal research workspace protected by one owner password. It is not an anonymously accessible multi-user service. A code and dependency review reduces risk; it does not certify that the application is security-proof.

## Implemented controls

- Hosted access fails closed without a sufficiently long owner password, session secret and private API token. Local password bypass is restricted to loopback development without Vercel. The launcher binds both servers to `127.0.0.1`.
- Sessions are signed, time limited, owner bound and stored in host-only HttpOnly cookies with SameSite Strict and Secure on hosted HTTPS. Password comparison is constant time. Password rotation invalidates the expected owner identity.
- Every API handler checks access. Mutating browser requests require the exact same Origin; the Python API additionally requires a server-only bearer token and owner fingerprint. Client-supplied authorisation headers are not forwarded upstream.
- The web proxy permits only configuration, stock calculation and the two download routes. It does not proxy arbitrary URLs or expose old saved-run, database, upload or browser key-setting endpoints.
- Provider keys remain in server environment variables. The browser receives only provider availability, public observations, calculations and encrypted opaque snapshots. AI generation is off by default and requires explicit per-report consent.
- Observation tokens use authenticated encryption, a time-to-live and owner/ticker binding. Token tampering, expiry and cross-owner reuse fail. There is no ID-based unauthorised download path or shared private-document store.
- Requests are bounded at 384 KiB. Ticker, input keys, finite numbers and document counts have limits. At most three blocking research workers run per backend instance; timed-out requests retain their slot until the blocking call completes.
- Official-source retrieval requires HTTPS on the company website supplied by the data source or SEC/NSE/BSE. Every DNS response and redirect is validated; the TCP destination is pinned to the checked public IP, with normal TLS hostname verification. Loopback/private/link-local/metadata destinations, credentials and custom ports are rejected. Environment proxies are not used.
- Retrieved files have byte, encoding, timeout, PDF-page, decoded-stream and text limits. Uploaded archives and arbitrary local files are not accepted. Source documents are data and cannot invoke tools or override model instructions.
- PDF rendering escapes text, uses a small markup whitelist and does not fetch remote images, execute HTML, include attachments or execute PDF scripts. Exported source links omit credentials and query strings; local paths are redacted. Provider/user strings are written as Excel strings rather than formulas; no macros or external workbook links are generated.
- Responses use no-store for private content, nosniff, framing restrictions, a nonce-based script CSP, restrictive browser permissions and referrer controls. Production HTTPS uses HSTS. Development has the additional permissions required by Next.js development tools.
- `.env`, `.private`, virtual environments, dependencies, local exports and build artifacts are excluded from source and cloud uploads. Only public market observations are cached on the server.

## Practical limits

The login throttle and worker limits apply per process/instance; Vercel firewall rules or another durable distributed limiter are needed to protect a publicly advertised deployment at scale. Signed sessions are not a multi-user identity system and there is no immediate per-session revocation store. An authorised owner can download and share public observations and their own research brief.

DNS calls and some third-party parsing operations cannot be forcibly cancelled inside Python threads. The outer timeout and retained worker slot bound concurrency, while network reads and parsers have their own limits. Official websites may deny automated requests. Yahoo is a secondary source and can return incomplete or inaccurate information; no data provider supplies a guarantee of financial correctness here.

AI prompt-injection instructions are isolated as untrusted evidence and no tool execution is enabled. A language model can still produce inaccurate interpretations; optional narrative requires review. A source website can link to content that has not been independently verified as a filing for the requested company or period.

The dependency audit records the versions checked and known advisories at that moment. Repeat audits, apply security updates and test the deployed environment regularly. A cloud penetration test, load test, data licence review, provider availability commitment and regulatory certification are outside the local verification performed here.

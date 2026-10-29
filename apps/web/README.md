# Astra Research frontend

Run commands from this directory:

```sh
npm ci
npm run dev
```

The app runs at http://127.0.0.1:3000. Start the Python research API on port 8000, copy `.env.example` to `.env.local`, and set `INTERNAL_API_TOKEN` to the same random value used by that API. The repository launcher configures these local secrets automatically. There is no worker, database, or browser-side provider key requirement.

`/models` retrieves real company financial observations and shows an editable five-year DCF, working-capital schedule, CAPM WACC, valuation bridge, and sensitivity analysis. Excel and PDF downloads use the same applied assumptions and encrypted financial snapshot. A fresh company input starts empty; unavailable source observations remain blank. `/methodology` explains the calculation and source conventions.

For Vercel, import the repository root (`.`) with Framework Preset **Services**, using the root `vercel.json`. Next.js is public and FastAPI stays private; the frontend's `PRIVATE_API_URL` is supplied by a service binding. Do not set it manually. Follow [the deployment guide](../../docs/DEPLOYMENT.md) to generate the owner password, session secret, shared API token and snapshot-encryption key privately, then add them through Vercel environment variables. Set `APP_ENV=production` and `APP_MODE=LIVE`. The hosted workspace stays closed until private access is configured. Provider keys belong in server environment variables. Never use `NEXT_PUBLIC_` for a secret.

The server proxy only permits the configuration, live model, Excel, and PDF endpoints. Owner sessions use signed, HttpOnly, SameSite Strict cookies with an eight-hour expiry. Mutating requests require the same origin, request bodies are limited to 384 KiB, and pages receive a fresh Content Security Policy nonce. Local no-password access is limited to loopback hosts outside Vercel; bind a local development server to `127.0.0.1`.

Validation:

```sh
npm run lint
npm run typecheck
npm test
npm run build
```

Use npm start to serve the production build. System fonts keep builds independent of remote font services.

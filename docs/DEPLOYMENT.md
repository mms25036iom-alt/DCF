# Deploying Astra on Vercel

The repository is prepared for Vercel's **Services beta**: Next.js is the public service, and FastAPI is reachable through a server-side service binding. The configuration follows the current [service configuration reference](https://vercel.com/docs/services/config-reference), [bindings documentation](https://vercel.com/docs/services/bindings), and [routing documentation](https://vercel.com/docs/services/routing). No live cloud deployment has been performed or claimed by the local checks.

## 1. Prepare the repository

Import the GitHub repository with **Root Directory: `.` (repository root)** and **Framework Preset: Services**. Leave build/install settings automatic for the declared services. Keep the root `vercel.json`. It declares `frontend` at `apps/web/` and `backend` at `.` with the ASGI entry point `services.serverless.app:app`. All public requests route to the frontend. Do not add a public rewrite to the Python service.

The backend has no PostgreSQL, Redis, Celery, persistent worker or durable filesystem requirement. It generates each export on demand. Yahoo's public cache uses temporary storage; cache availability is an optimisation, not required storage. Snapshot observations are encrypted into a short-lived opaque token held in browser memory and bound to the owner identity.

The project contains local ignored `.private` research and recovery records. They must remain private. `.gitignore` and `.vercelignore` exclude environment files, private artifacts, build outputs, local environments, tests and generated exports. Review the exact files being committed and uploaded before deployment.

## 2. Generate and set credentials

Run from the root:

```powershell
.venv/Scripts/python.exe scripts/generate_secrets.py
```

This writes `.private/deployment.env` and prints its location without printing values. Open it privately and copy the four generated entries into the Vercel project's server environment variables for the environments you intend to use. Do not upload that file, put secrets in `vercel.json`, paste credentials into client code or use a `NEXT_PUBLIC_` prefix.

| Variable | Purpose |
| --- | --- |
| `APP_ACCESS_PASSWORD` | Owner login; at least 20 characters. Keep the generated random value. |
| `APP_SESSION_SECRET` | Signs browser sessions and derives owner identity; at least 32 characters. |
| `INTERNAL_API_TOKEN` | Authenticates the Next.js server to FastAPI; at least 32 characters. |
| `SNAPSHOT_ENCRYPTION_KEY` | Fernet key for encrypted observation snapshots; generated in the correct format. |
| `APP_ENV` | Set to `production`. |
| `APP_MODE` | Set to `LIVE`. |
| `MODEL_ROUTER_PROVIDER` | Set to `none` for data-only research without an AI key. |

Apply the secrets consistently to both services. `PRIVATE_API_URL` is created by the frontend service binding at runtime; do not set it to localhost or a stale deployment URL. No market-data API key is needed. The app fails closed if required hosted credentials are missing.

Optional Groq narrative requires `MODEL_ROUTER_PROVIDER=groq`, your `GROQ_API_KEY`, and a supported `GROQ_MODEL_ID` (the locally tested model is `openai/gpt-oss-120b`). Set `MODEL_CONTEXT_MAX_CHARS=12000` and `MODEL_MAX_OUTPUT_TOKENS=1500` to bound the evidence and response size. Store the key only in Vercel's server environment variables. The owner must explicitly opt in before each report sends the research brief, financial observations and attached public excerpts to the provider. Provider quotas and charges are governed by your account. Ollama's loopback endpoint is useful locally; it is not a remote model server reachable from Vercel.

## 3. Build and verify

Import the repository into Vercel and use its Services configuration. Select compatible Node/Python versions supported by the platform. Use the committed npm and uv lockfiles; the Python requirements file mirrors runtime pins. The API route requests a function duration suitable for on-demand exports, subject to your plan's limits. Request sizes are capped at 384 KiB in the app, and generated files are intended to remain well within platform response limits.

After deploying to a preview, verify:

1. An unauthenticated `/models` visit redirects to owner login; `/api/v1/config` rejects anonymous calls.
2. Owner login works over HTTPS. Cookies are HttpOnly, Secure and SameSite Strict. Logout clears the session.
3. Enter a real listing, confirm identity and source dates, then download Excel and PDF after applying an assumption change.
4. The direct Python service has no public route, and API calls without the internal bearer token are rejected.
5. No browser responses, JS bundles, logs or download metadata contain a provider key, internal token, session secret or local path.

Vercel Services is a beta platform feature. The current [Services pricing](https://vercel.com/docs/services/pricing) includes Hobby, and [Fluid Compute duration limits](https://vercel.com/docs/functions/configuring-functions/duration) allow the requested 300-second maximum on Hobby. [Hobby](https://vercel.com/docs/plans/hobby) is for personal, noncommercial use and has usage quotas; account eligibility and a live deployment have not been verified here. Free Yahoo access and hosting quotas do not promise uninterrupted service. Enable suitable Vercel firewall rules and spending limits; the application-level login throttle is per instance, not a globally coordinated abuse-control service.

## Rotation and recovery

Changing `APP_ACCESS_PASSWORD` changes the expected owner identity and invalidates existing sessions and model tokens. Rotating `APP_SESSION_SECRET` invalidates sessions and changes the owner identity. Rotating `SNAPSHOT_ENCRYPTION_KEY` invalidates outstanding model tokens; the user reloads the listing. Rotate `INTERNAL_API_TOKEN` in both services together. If a secret has ever been publicly committed, deleting the file is insufficient: revoke it and rotate the affected credential.

No multi-user account database or persistent research history is included. Downloads belong to the owner; each user must save files they want to keep. Adding public registration requires separate identities, authorisation policies, distributed limits and a suitable source-data licence.

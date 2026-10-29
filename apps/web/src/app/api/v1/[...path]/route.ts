import { NextRequest, NextResponse } from "next/server";
import { allowedApiPath, backendConfiguration, readLimitedBody, requestAuthorization, safeAttachmentHeader, sameOrigin, workspaceOwner } from "@/lib/server-security.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;
type Context = { params: Promise<{ path: string[] }> };
const noStore = { "Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff" };

function failure(detail: string, status: number) { return NextResponse.json({ detail }, { status, headers: noStore }); }

async function forward(request: NextRequest, context: Context) {
  const { path: segments } = await context.params;
  const path = segments.join("/");
  if (!allowedApiPath(path, request.method) || request.nextUrl.search) return failure("This research endpoint is not available.", 404);
  const access = requestAuthorization(request);
  if (!access.configured) return failure("Private workspace access is not configured.", 503);
  if (!access.authenticated) return failure("Your session has expired. Sign in to continue.", 401);
  if (request.method === "POST" && !sameOrigin(request)) return failure("This request must come from the workspace.", 403);
  let body: string | undefined;
  if (request.method === "POST") {
    if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || "")) return failure("Send the research request as JSON.", 415);
    try {
      const bytes = await readLimitedBody(request);
      const parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return failure("A research request must be a JSON object.", 400);
      body = JSON.stringify(parsed);
    } catch (error) { return failure(error instanceof Error && error.message === "BODY_TOO_LARGE" ? "The research request is too large." : "The research request contains invalid JSON.", error instanceof Error && error.message === "BODY_TOO_LARGE" ? 413 : 400); }
  }
  let service: ReturnType<typeof backendConfiguration>;
  try { service = backendConfiguration(); } catch { return failure("The private research service has not been configured.", 503); }
  try {
    const upstream = await fetch(`${service.base}/v1/${path}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Accept: path === "equity/report" ? "application/pdf" : path.endsWith("/export") ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "application/json", Authorization: `Bearer ${service.token}`, "X-workspace-owner": workspaceOwner() },
      body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(240000),
    });
    if (!upstream.ok) {
      const defaults: Record<number, string> = { 400: "The research request could not be processed.", 404: "No verified financial data was found for this ticker.", 409: "The financial snapshot has expired. Refresh the company data and try again.", 410: "The financial snapshot has expired. Refresh the company data and try again.", 422: "Check the ticker, valuation assumptions and source URLs.", 429: "The data source is temporarily rate limited. Please try again shortly.", 503: "The financial data source is temporarily unavailable. Please try again." };
      let detail = defaults[upstream.status] || "The research service could not complete this request. Please try again.";
      try {
        const payload = await upstream.json();
        if (typeof payload.detail === "string" && [400, 404, 409, 410, 422, 429, 503].includes(upstream.status)) {
          detail = payload.detail.slice(0, 800);
          for (const secret of [service.base, service.token, process.env.APP_ACCESS_PASSWORD, process.env.APP_SESSION_SECRET]) if (secret) detail = detail.split(secret).join("[redacted]");
        }
      } catch { /* Only safe public errors are returned. */ }
      return failure(detail, [400, 404, 409, 410, 422, 429, 503].includes(upstream.status) ? upstream.status : 502);
    }
    if (path === "config") {
      const value = await upstream.json();
      return NextResponse.json({ app_mode: "LIVE", live_ready: value.live_ready === true, model_provider: ["groq", "ollama"].includes(value.model_provider) ? value.model_provider : "none", ai_available: value.ai_available === true }, { headers: noStore });
    }
    const type = upstream.headers.get("content-type") || "";
    const expected = path === "equity/report" ? "application/pdf" : path.endsWith("/export") ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "application/json";
    if (!type.toLowerCase().startsWith(expected)) return failure("The research service returned an unexpected document. Please try again.", 502);
    const responseHeaders = new Headers({ ...noStore, "Content-Type": expected });
    if (expected !== "application/json") {
      const extension = path === "equity/report" ? "pdf" : "xlsx";
      responseHeaders.set("Content-Disposition", safeAttachmentHeader(upstream.headers.get("content-disposition"), extension));
    }
    return new Response(upstream.body, { headers: responseHeaders });
  } catch (error) {
    return failure(error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name) ? "The data source took too long to respond. Please try again." : "The private research service is temporarily unavailable.", error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name) ? 504 : 502);
  }
}

export const GET = forward;
export const POST = forward;

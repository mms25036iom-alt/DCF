import { NextRequest, NextResponse } from "next/server";
import { createSession, publicRequestUrl, readLimitedBody, requestAuthorization, sameOrigin, SESSION_COOKIE, SESSION_SECONDS, validLoginPassword } from "@/lib/server-security.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const attempts = new Map<string, { count: number; until: number }>();

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "This request must come from the workspace." }, { status: 403 });
  const publicUrl = publicRequestUrl(request);
  const access = requestAuthorization(request);
  if (!access.authentication_required) return NextResponse.redirect(new URL("/models", publicUrl), 303);
  if (!access.configured) return NextResponse.json({ detail: "Private workspace access is not configured." }, { status: 503 });
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/x-www-form-urlencoded")) return NextResponse.json({ detail: "Use the sign-in form." }, { status: 415 });
  const now = Date.now();
  const client = (request.headers.get("x-forwarded-for") || "local").split(",")[0].trim().slice(0, 80);
  if (attempts.size >= 1000) for (const [key, value] of attempts) if (value.until <= now) attempts.delete(key);
  const attempt = attempts.get(client);
  if (!attempt && attempts.size >= 1000) return NextResponse.redirect(new URL("/login?error=limited", publicUrl), 303);
  if (attempt && attempt.until > now && attempt.count >= 10) return NextResponse.redirect(new URL("/login?error=limited", publicUrl), 303);
  let password = "";
  try {
    const body = await readLimitedBody(request, 8192);
    const fields = new URLSearchParams(new TextDecoder().decode(body));
    if (fields.getAll("password").length === 1) password = fields.get("password") || "";
  } catch { return NextResponse.json({ detail: "The sign-in request is too large." }, { status: 413 }); }
  if (!validLoginPassword(password)) {
    attempts.set(client, { count: attempt && attempt.until > now ? attempt.count + 1 : 1, until: attempt && attempt.until > now ? attempt.until : now + 15 * 60 * 1000 });
    await new Promise(resolve => setTimeout(resolve, 300));
    return NextResponse.redirect(new URL("/login?error=invalid", publicUrl), 303);
  }
  attempts.delete(client);
  const response = NextResponse.redirect(new URL("/models", publicUrl), 303);
  response.cookies.set(SESSION_COOKIE, createSession(), { httpOnly: true, secure: publicUrl.protocol === "https:", sameSite: "strict", path: "/", maxAge: SESSION_SECONDS });
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

import { randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { publicRequestUrl, requestAuthorization, securityHeaders } from "@/lib/server-security.mjs";

export function proxy(request: NextRequest) {
  const nonce = randomBytes(18).toString("base64");
  let publicUrl: URL;
  try { publicUrl = publicRequestUrl(request); }
  catch { return NextResponse.json({ detail: "The request host is invalid." }, { status: 400 }); }
  const headers = securityHeaders(nonce, process.env.NODE_ENV === "development", publicUrl.protocol === "https:");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", headers["Content-Security-Policy"]);
  const access = requestAuthorization(request);
  let response: NextResponse;
  if (!request.nextUrl.pathname.startsWith("/api/") && request.nextUrl.pathname !== "/login" && !access.authenticated) {
    response = NextResponse.redirect(new URL("/login", publicUrl));
  } else {
    response = NextResponse.next({ request: { headers: requestHeaders } });
  }
  for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
  return response;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"] };

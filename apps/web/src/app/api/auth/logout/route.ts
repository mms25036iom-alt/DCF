import { NextRequest, NextResponse } from "next/server";
import { publicRequestUrl, requestAuthorization, sameOrigin, SESSION_COOKIE } from "@/lib/server-security.mjs";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "This request must come from the workspace." }, { status: 403 });
  const publicUrl = publicRequestUrl(request);
  const access = requestAuthorization(request);
  const response = NextResponse.redirect(new URL(access.authentication_required ? "/login" : "/models", publicUrl), 303);
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, secure: publicUrl.protocol === "https:", sameSite: "strict", path: "/", maxAge: 0 });
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

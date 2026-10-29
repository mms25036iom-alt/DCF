import { NextRequest, NextResponse } from "next/server";
import { requestAuthorization } from "@/lib/server-security.mjs";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { authenticated, authentication_required } = requestAuthorization(request);
  return NextResponse.json({ authenticated, authentication_required }, { headers: { "Cache-Control": "private, no-store" } });
}

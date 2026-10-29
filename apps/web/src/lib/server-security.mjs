import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "astra_owner_session";
export const SESSION_SECONDS = 8 * 60 * 60;
export const MAX_API_BODY_BYTES = 384 * 1024;
const LOCAL_OWNER = createHash("sha256").update("astra-local-owner-v1").digest("hex");

/** @param {Record<string, string | undefined>} env */
export function accessConfiguration(env = process.env) {
  const hosted = Boolean(env.VERCEL) || env.APP_ENV === "production";
  const password = env.APP_ACCESS_PASSWORD || "";
  const secret = env.APP_SESSION_SECRET || "";
  const required = hosted || Boolean(password);
  const ready = !required || (password.length >= 20 && password.length <= 512 && secret.length >= 32);
  return { hosted, password, secret, required, ready };
}

export function loopbackHostname(hostname) {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(String(hostname).toLowerCase());
}

export function fixedTimeEqual(left, right) {
  const a = createHash("sha256").update(String(left)).digest();
  const b = createHash("sha256").update(String(right)).digest();
  return timingSafeEqual(a, b);
}

export function workspaceOwner(env = process.env) {
  const configuration = accessConfiguration(env);
  if (!configuration.required) return LOCAL_OWNER;
  if (!configuration.ready) throw new Error("Private application access is not configured.");
  return createHmac("sha256", configuration.secret).update("workspace-owner-v1:").update(configuration.password).digest("hex");
}

export function cookieValue(header, name = SESSION_COOKIE) {
  const values = String(header || "").split(";").map(value => value.trim()).filter(value => value.startsWith(name + "="));
  return values.length === 1 ? values[0].slice(name.length + 1) : "";
}

export function createSession(env = process.env, now = Math.floor(Date.now() / 1000)) {
  const configuration = accessConfiguration(env);
  if (!configuration.ready || !configuration.required) throw new Error("Private application access is not configured.");
  const payload = Buffer.from(JSON.stringify({ v: 1, iat: now, exp: now + SESSION_SECONDS, owner: workspaceOwner(env), nonce: randomBytes(18).toString("base64url") })).toString("base64url");
  const signature = createHmac("sha256", configuration.secret).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

export function verifySession(token, env = process.env, now = Math.floor(Date.now() / 1000)) {
  const configuration = accessConfiguration(env);
  if (!configuration.ready || !configuration.required || typeof token !== "string" || token.length > 1024) return false;
  const parts = token.split(".");
  if (parts.length !== 2 || !/^[A-Za-z0-9_-]+$/.test(parts[0]) || !/^[A-Za-z0-9_-]{43}$/.test(parts[1])) return false;
  const expected = createHmac("sha256", configuration.secret).update(parts[0]).digest("base64url");
  if (!fixedTimeEqual(expected, parts[1])) return false;
  try {
    const value = JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8"));
    return value.v === 1 && Number.isInteger(value.iat) && Number.isInteger(value.exp) && value.exp > now && value.iat <= now + 30 && value.exp - value.iat === SESSION_SECONDS && fixedTimeEqual(value.owner, workspaceOwner(env)) && /^[A-Za-z0-9_-]{24}$/.test(value.nonce);
  } catch { return false; }
}

export function authorization(url, cookieHeader, env = process.env) {
  const configuration = accessConfiguration(env);
  const local = !configuration.hosted && !configuration.required && loopbackHostname(new URL(url).hostname);
  const authenticated = local || (configuration.ready && verifySession(cookieValue(cookieHeader), env));
  return { authenticated, configured: configuration.ready && (configuration.required || local), authentication_required: !local };
}

export function validLoginPassword(value, env = process.env) {
  const configuration = accessConfiguration(env);
  return configuration.required && configuration.ready && typeof value === "string" && value.length <= 512 && fixedTimeEqual(value, configuration.password);
}

/** Next may build request.url from its internal listener rather than the browser Host.
 * Only the HTTP Host identifies this request's authority; forwarded headers never do.
 * @param {{url:string, headers: {get:(name:string)=>string|null}}} request
 * @param {Record<string, string | undefined>} env
 */
export function publicRequestUrl(request, env = process.env) {
  const supplied = request.headers.get("host");
  if (!supplied || supplied.length > 320 || /[\s,@/\\?#%]/.test(supplied)) throw new Error("INVALID_HOST");
  const authority = new URL(`http://${supplied}`);
  if (authority.username || authority.password || authority.pathname !== "/" || authority.search || authority.hash || authority.host.toLowerCase() !== supplied.toLowerCase()) throw new Error("INVALID_HOST");
  const internal = new URL(request.url);
  if (!["http:", "https:"].includes(internal.protocol)) throw new Error("INVALID_HOST");
  const protocol = Boolean(env.VERCEL) || (accessConfiguration(env).hosted && !loopbackHostname(authority.hostname)) ? "https:" : internal.protocol;
  const result = new URL(`${protocol}//${authority.host}`);
  result.pathname = internal.pathname;
  result.search = internal.search;
  return result;
}

/** @param {{url:string, headers: {get:(name:string)=>string|null}}} request */
export function requestAuthorization(request, env = process.env) {
  try { return authorization(publicRequestUrl(request, env).href, request.headers.get("cookie"), env); }
  catch { return { authenticated: false, configured: false, authentication_required: true }; }
}

export function sameOrigin(request, env = process.env) {
  const supplied = request.headers.get("origin");
  if (!supplied || supplied === "null" || request.headers.get("sec-fetch-site") === "cross-site") return false;
  try {
    const origin = new URL(supplied);
    return !origin.username && !origin.password && !origin.search && !origin.hash && origin.pathname === "/" && supplied === origin.origin && origin.origin === publicRequestUrl(request, env).origin;
  } catch { return false; }
}

/** @param {Request} request */
export async function readLimitedBody(request, maximum = MAX_API_BODY_BYTES) {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > maximum)) throw new Error("BODY_TOO_LARGE");
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) { await reader.cancel(); throw new Error("BODY_TOO_LARGE"); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}

export function allowedApiPath(path, method) {
  return method === "GET" ? path === "config" : method === "POST" && ["stocks/model", "stocks/model/export", "equity/report"].includes(path);
}

export function safeAttachmentHeader(value, extension) {
  const suffix = extension === "pdf" ? "pdf" : "xlsx";
  return typeof value === "string" && new RegExp(`^attachment;\\s*filename="[A-Za-z0-9._-]{1,90}\\.${suffix}"$`, "i").test(value)
    ? value : `attachment; filename="astra-research.${suffix}"`;
}

export function backendConfiguration(env = process.env) {
  const hosted = accessConfiguration(env).hosted;
  const base = env.PRIVATE_API_URL || (!hosted ? env.API_BASE_URL || "http://127.0.0.1:8000" : "");
  const token = env.INTERNAL_API_TOKEN || "";
  if (!base || token.length < 32) throw new Error("Research service access is not configured.");
  const url = new URL(base);
  if (url.username || url.password || url.search || url.hash || (url.protocol !== "https:" && !(url.protocol === "http:" && !hosted && loopbackHostname(url.hostname)))) throw new Error("Research service access is not configured.");
  return { base: url.href.replace(/\/$/, ""), token };
}

export function securityHeaders(nonce, development = false, secure = true) {
  return {
    "Content-Security-Policy": `default-src 'self'; script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}; style-src 'self' 'nonce-${nonce}'; style-src-attr 'none'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'${development ? " ws: wss:" : ""}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'${secure ? "; upgrade-insecure-requests" : ""}`,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cache-Control": "private, no-store, max-age=0",
    ...(secure ? { "Strict-Transport-Security": "max-age=31536000; includeSubDomains" } : {}),
  };
}

import test from "node:test";
import assert from "node:assert/strict";
import { accessConfiguration, allowedApiPath, authorization, backendConfiguration, cookieValue, createSession, MAX_API_BODY_BYTES, publicRequestUrl, readLimitedBody, requestAuthorization, safeAttachmentHeader, sameOrigin, SESSION_COOKIE, SESSION_SECONDS, validLoginPassword, verifySession, workspaceOwner } from "../src/lib/server-security.mjs";
import { safeReportUrl } from "../src/lib/safe-url.mjs";

const env = { VERCEL: "1", APP_ACCESS_PASSWORD: "private-test-password-with-32-chars", APP_SESSION_SECRET: "test-session-secret-at-least-32-characters", PRIVATE_API_URL: "https://private.example.test", INTERNAL_API_TOKEN: "test-internal-token-at-least-32-characters" };

test("hosted access fails closed until both owner credentials are strong", () => {
  assert.equal(accessConfiguration({ VERCEL: "1" }).ready, false);
  assert.equal(accessConfiguration({ APP_ENV: "production" }).ready, false);
  assert.equal(authorization("http://localhost/models", "", { APP_ENV: "production" }).authenticated, false);
  assert.equal(accessConfiguration({ ...env, APP_ACCESS_PASSWORD: "short" }).ready, false);
  assert.equal(accessConfiguration({ ...env, APP_SESSION_SECRET: "short" }).ready, false);
  assert.equal(validLoginPassword(env.APP_ACCESS_PASSWORD, env), true);
  assert.equal(validLoginPassword("incorrect", env), false);
  assert.equal(authorization("https://localhost/models", "", { VERCEL: "1" }).authenticated, false);
});

test("sessions reject modification, expiry and owner-secret rotation", () => {
  const now = Math.floor(Date.now() / 1000);
  const token = createSession(env, now);
  assert.equal(verifySession(token, env, now), true);
  assert.equal(verifySession(token, env, now + SESSION_SECONDS), false);
  assert.equal(verifySession(token, env, now - 31), false);
  assert.equal(verifySession(token + "x", env, now), false);
  assert.equal(verifySession(token.replace(/^./, token[0] === "a" ? "b" : "a"), env, now), false);
  assert.equal(verifySession(token, { ...env, APP_SESSION_SECRET: "a-different-session-secret-with-more-than-32-characters" }, now), false);
  assert.equal(verifySession(token, { ...env, APP_ACCESS_PASSWORD: "a-different-workspace-password-12345" }, now), false);
  assert.match(workspaceOwner(env), /^[a-f0-9]{64}$/);
  assert.equal(authorization("https://research.example.test/models", `${SESSION_COOKIE}=${token}`, env).authenticated, true);
});

test("development bypass is limited to local loopback hosts", () => {
  for (const host of ["localhost", "127.0.0.1", "[::1]"]) assert.equal(authorization(`http://${host}:3000/models`, "", {}).authenticated, true);
  for (const host of ["public.example.test", "localhost.example.test", "192.168.1.2"]) assert.equal(authorization(`http://${host}:3000/models`, "", {}).authenticated, false);
  assert.equal(authorization("http://localhost:3000/models", "", { ...env, VERCEL: undefined }).authenticated, false);
  assert.equal(cookieValue(`${SESSION_COOKIE}=first; ${SESSION_COOKIE}=second`), "");
});

test("mutation requests must come from the exact workspace origin", () => {
  const request = (origin, site = "same-origin") => new Request("https://research.example.test/api/v1/stocks/model", { method: "POST", headers: { host: "research.example.test", origin, "sec-fetch-site": site } });
  assert.equal(sameOrigin(request("https://research.example.test")), true);
  assert.equal(sameOrigin(request("https://elsewhere.example.test")), false);
  assert.equal(sameOrigin(request("http://research.example.test")), false);
  assert.equal(sameOrigin(request("null")), false);
  assert.equal(sameOrigin(request("https://research.example.test", "cross-site")), false);
  assert.equal(sameOrigin(new Request("https://research.example.test/api")), false);
  for (const origin of ["https://user@research.example.test", "https://research.example.test/path", "https://research.example.test?query=value", "https://research.example.test#fragment", "https://research.example.test/", "https://research.example.test https://attacker.test"]) assert.equal(sameOrigin(request(origin)), false);
});

test("public Host resolves Next's internal-localhost URL without trusting forwarded headers", () => {
  const local = new Request("http://localhost:3001/api/auth/login", { headers: { host: "127.0.0.1:3001", origin: "http://127.0.0.1:3001", "x-forwarded-host": "attacker.test", "x-forwarded-proto": "https" } });
  assert.equal(publicRequestUrl(local, {}).origin, "http://127.0.0.1:3001");
  assert.equal(sameOrigin(local, { APP_ENV: "production" }), true);
  assert.equal(requestAuthorization(local, {}).authenticated, true);
  const remote = new Request("http://localhost:3000/models", { headers: { host: "research.example.test", origin: "https://research.example.test", "x-forwarded-host": "localhost", "x-forwarded-proto": "http" } });
  assert.equal(requestAuthorization(remote, {}).authenticated, false);
  assert.equal(sameOrigin(remote, { APP_ENV: "production" }), true);
  assert.equal(sameOrigin(remote, { VERCEL: "1" }), true);
  assert.equal(sameOrigin(new Request(remote, { headers: { host: "research.example.test", origin: "http://research.example.test" } }), { APP_ENV: "production" }), false);
  assert.equal(publicRequestUrl(new Request("http://localhost:3000/login", { headers: { host: "[::1]:3000" } }), {}).host, "[::1]:3000");
  assert.equal(publicRequestUrl(new Request("http://localhost:3000//localhost/models?x=1", { headers: { host: "research.example.test" } }), {}).host, "research.example.test");
  for (const host of ["localhost,attacker.test", "user@localhost", "localhost/attacker", "localhost\\attacker", "localhost:3000?foo", "localhost:3000#foo", "2130706433", "localhost:"]) assert.throws(() => publicRequestUrl(new Request("http://localhost:3000/models", { headers: { host } }), {}));
  assert.equal(requestAuthorization(new Request("http://localhost:3000/models"), {}).authenticated, false);
});

test("document filenames retain ticker names while rejecting unsafe response headers", () => {
  assert.equal(safeAttachmentHeader('attachment; filename="reliance.ns-dcf-model.xlsx"', "xlsx"), 'attachment; filename="reliance.ns-dcf-model.xlsx"');
  assert.equal(safeAttachmentHeader('attachment; filename="tcs.ns-equity-report.pdf"', "pdf"), 'attachment; filename="tcs.ns-equity-report.pdf"');
  for (const header of ['inline; filename="evil.pdf"', 'attachment; filename="../evil.pdf"', 'attachment; filename="evil.pdf.exe"', 'attachment; filename="evil.pdf"; arbitrary=value', 'attachment; filename="evil.xlsx"', 'attachment; filename="evil.pdf"\r\nX-Test: injected', `attachment; filename="${"x".repeat(91)}.pdf"`]) assert.equal(safeAttachmentHeader(header, "pdf"), 'attachment; filename="astra-research.pdf"');
});

test("proxy only permits the live model and document endpoints", () => {
  assert.equal(allowedApiPath("config", "GET"), true);
  for (const path of ["stocks/model", "stocks/model/export", "equity/report"]) assert.equal(allowedApiPath(path, "POST"), true);
  for (const [path, method] of [["settings", "GET"], ["config", "POST"], ["research-runs", "POST"], ["stocks/model", "GET"], ["../settings", "POST"], ["equity/report", "DELETE"]]) assert.equal(allowedApiPath(path, method), false);
});

test("body limit counts actual bytes, including chunked requests", async () => {
  const small = new Request("http://localhost/api", { method: "POST", body: "{}" });
  assert.equal(new TextDecoder().decode(await readLimitedBody(small)), "{}");
  const oversizedHeader = new Request("http://localhost/api", { method: "POST", headers: { "content-length": String(MAX_API_BODY_BYTES + 1) }, body: "{}" });
  await assert.rejects(readLimitedBody(oversizedHeader), /BODY_TOO_LARGE/);
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(9)); controller.enqueue(new Uint8Array(9)); controller.close(); } });
  const chunked = new Request("http://localhost/api", { method: "POST", body: stream, duplex: "half" });
  await assert.rejects(readLimitedBody(chunked, 16), /BODY_TOO_LARGE/);
});

test("backend credentials stay server-side and hosted transport requires HTTPS", () => {
  assert.equal(backendConfiguration(env).base, "https://private.example.test");
  assert.equal(backendConfiguration({ INTERNAL_API_TOKEN: env.INTERNAL_API_TOKEN }).base, "http://127.0.0.1:8000");
  for (const PRIVATE_API_URL of ["http://private.example.test", "http://127.0.0.1:8000", "https://user:password@example.test", "https://example.test?token=value"]) assert.throws(() => backendConfiguration({ ...env, PRIVATE_API_URL }));
  assert.throws(() => backendConfiguration({ ...env, INTERNAL_API_TOKEN: "short" }));
  assert.throws(() => backendConfiguration({ VERCEL: "1", INTERNAL_API_TOKEN: env.INTERNAL_API_TOKEN, API_BASE_URL: "https://ignored.example.test" }));
  assert.throws(() => backendConfiguration({ APP_ENV: "production", INTERNAL_API_TOKEN: env.INTERNAL_API_TOKEN, API_BASE_URL: "http://localhost:8000" }));
});

test("source links reject script schemes, credentials and control characters", () => {
  assert.equal(safeReportUrl("https://finance.yahoo.com/quote/TCS.NS/"), "https://finance.yahoo.com/quote/TCS.NS/");
  for (const value of ["javascript:alert(1)", "//evil.example.test", "https://user:pass@example.test", "https://example.test/\npath", "data:text/html,test", "#section"]) assert.equal(safeReportUrl(value), "");
});

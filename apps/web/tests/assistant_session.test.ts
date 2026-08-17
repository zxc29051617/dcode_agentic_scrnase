/**
 * Unit tests for the per-session assistant store and the cookie security
 * helper it depends on.
 *
 * The property under test that matters most is isolation: two different
 * session ids must never see or overwrite each other's config. That is the
 * whole difference between "each visitor brings their own key safely" and
 * the shared-machine failure mode of a single server-side variable that the
 * last person to type a key silently overwrites for everyone.
 *
 * Run with:
 *     npm run test:unit
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  createAssistantSessionStore,
  toStatus,
} from "../lib/assistantSession.ts";
import { isRequestSecure, secureSessionCookieOptions } from "../lib/cookieSecurity.ts";

// `isRequestSecure`/`secureSessionCookieOptions` only ever touch
// `request.headers.get(...)` and `request.nextUrl.protocol`, so a minimal
// object with that shape stands in for a real `NextRequest` here. Importing
// the real `next/server` module fails to resolve under
// `--conditions=react-server` (see assistant_model.test.ts's docstring for
// why that flag is needed at all) — the same reason no other test file in
// this project imports it directly.
type FakeNextRequest = { headers: { get(name: string): string | null }; nextUrl: { protocol: string } };

// --- isolation: the property this whole feature exists for -----------------

test("two session ids never see each other's config", () => {
  const store = createAssistantSessionStore();
  store.set("session-a", { provider: "openai", model: "gpt-4o-mini", apiKey: "sk-aaaa" });
  store.set("session-b", { provider: "local", model: "gpt-oss:120b" });

  const a = store.get("session-a");
  const b = store.get("session-b");
  assert.equal(a?.provider, "openai");
  assert.equal(b?.provider, "local");
  assert.notEqual(JSON.stringify(a), JSON.stringify(b));
});

test("setting one session's config does not alter another's", () => {
  const store = createAssistantSessionStore();
  store.set("session-a", { provider: "openai", model: "gpt-4o", apiKey: "sk-original" });
  store.set("session-b", { provider: "openai", model: "gpt-4o", apiKey: "sk-different" });

  // This is the exact bug a single shared variable would produce: session
  // b's save silently becoming session a's key too.
  const a = store.get("session-a");
  assert.equal(a?.provider, "openai");
  assert.ok(a && "apiKey" in a && a.apiKey === "sk-original");
});

test("an unknown session id returns null, not a default", () => {
  const store = createAssistantSessionStore();
  assert.equal(store.get("never-set"), null);
  assert.equal(store.get(undefined), null);
  assert.equal(store.get(null), null);
});

test("clearing one session does not affect another", () => {
  const store = createAssistantSessionStore();
  store.set("session-a", { provider: "local", model: "m1" });
  store.set("session-b", { provider: "local", model: "m2" });
  store.clear("session-a");
  assert.equal(store.get("session-a"), null);
  assert.equal(store.get("session-b")?.model, "m2");
});

test("replacing a session's config overwrites, not merges", () => {
  const store = createAssistantSessionStore();
  store.set("session-a", { provider: "openai", model: "gpt-4o", apiKey: "sk-old" });
  store.set("session-a", { provider: "local", model: "gpt-oss:120b" });
  const a = store.get("session-a");
  assert.equal(a?.provider, "local");
  assert.ok(a && !("apiKey" in a));
});

// --- TTL: an idle session's key does not outlive its process forever -------

test("an expired session is dropped and returns null", () => {
  const store = createAssistantSessionStore(1); // 1ms TTL
  store.set("session-a", { provider: "local", model: "m1" });
  const start = Date.now();
  while (Date.now() - start < 5) {
    /* busy-wait past the 1ms TTL without relying on a mockable clock */
  }
  assert.equal(store.get("session-a"), null);
});

test("reading a session refreshes its TTL rather than letting it expire mid-use", () => {
  const store = createAssistantSessionStore(50);
  store.set("session-a", { provider: "local", model: "m1" });
  const start = Date.now();
  while (Date.now() - start < 30) {
    /* still within the 50ms window */
  }
  // Reading here should reset the clock...
  assert.notEqual(store.get("session-a"), null);
  const secondStart = Date.now();
  while (Date.now() - secondStart < 30) {
    /* another 30ms — 60ms total, past the original TTL if it had not reset */
  }
  assert.notEqual(store.get("session-a"), null, "the read above should have refreshed the TTL");
});

// --- the key can never leave through the status shape -----------------------

test("toStatus never includes the api key, for openai sessions", () => {
  const status = toStatus({ provider: "openai", model: "gpt-4o", apiKey: "sk-should-never-appear" });
  const serialized = JSON.stringify(status);
  assert.ok(!serialized.includes("sk-should-never-appear"));
  assert.deepEqual(status, { active: true, provider: "openai", model: "gpt-4o", hasApiKey: true });
});

test("toStatus reports hasApiKey false for local sessions", () => {
  const status = toStatus({ provider: "local", model: "gpt-oss:120b" });
  assert.deepEqual(status, { active: true, provider: "local", model: "gpt-oss:120b", hasApiKey: false });
});

test("toStatus of null is inactive", () => {
  assert.deepEqual(toStatus(null), { active: false });
});

// --- store size, for the tests above to trust their own isolation claims ---

test("independent store instances never share state", () => {
  const storeA = createAssistantSessionStore();
  const storeB = createAssistantSessionStore();
  storeA.set("x", { provider: "local", model: "m" });
  assert.equal(storeA.size(), 1);
  assert.equal(storeB.size(), 0);
});

// --- cookie security -----------------------------------------------------

function req(url: string, headers: Record<string, string> = {}): FakeNextRequest {
  const headerMap = new Map(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    headers: { get: (name: string) => headerMap.get(name.toLowerCase()) ?? null },
    nextUrl: { protocol: new URL(url).protocol },
  };
}

test("a plain http request is not treated as secure", () => {
  assert.equal(isRequestSecure(req("http://localhost:3000/api/assistant-session")), false);
});

test("a direct https request is treated as secure", () => {
  assert.equal(isRequestSecure(req("https://example.com/api/assistant-session")), true);
});

test("x-forwarded-proto: https is trusted when present, even over a plain-http direct connection", () => {
  const request = req("http://localhost:3000/api/assistant-session", { "x-forwarded-proto": "https" });
  assert.equal(isRequestSecure(request), true);
});

test("x-forwarded-proto: http overrides an https direct connection reading (proxy is the source of truth)", () => {
  const request = req("https://localhost:3000/api/assistant-session", { "x-forwarded-proto": "http" });
  assert.equal(isRequestSecure(request), false);
});

test("cookie options are always httpOnly and sameSite=strict regardless of transport", () => {
  const plain = secureSessionCookieOptions(req("http://localhost:3000/x"), 1000);
  const tls = secureSessionCookieOptions(req("https://example.com/x"), 1000);
  assert.equal(plain.httpOnly, true);
  assert.equal(plain.sameSite, "strict");
  assert.equal(plain.secure, false);
  assert.equal(tls.httpOnly, true);
  assert.equal(tls.sameSite, "strict");
  assert.equal(tls.secure, true);
});

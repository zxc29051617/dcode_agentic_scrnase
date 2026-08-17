import { NextRequest, NextResponse } from "next/server";
import {
  SESSION_COOKIE,
  SESSION_TTL_MS,
  assistantSessions,
  newSessionId,
  toStatus,
} from "@/lib/assistantSession";
import { secureSessionCookieOptions } from "@/lib/cookieSecurity";

/**
 * Per-browser assistant configuration: which provider, which model, and —
 * only for `openai` — the visitor's own API key, held only for the
 * lifetime of their session. See `lib/assistantSession.ts` for the
 * isolation guarantee this depends on.
 *
 * GET    — this session's current status, never the key itself
 * POST   — set (or replace) this session's config
 * DELETE — forget this session's config; the "clear my key" action
 */

export async function GET(request: NextRequest) {
  const sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  const config = assistantSessions.get(sessionId);
  return NextResponse.json(toStatus(config));
}

/**
 * A plausibility check, not a real validation — OpenAI's own API is what
 * actually validates a key, on the next request that uses it. This exists
 * only to reject an obviously-wrong paste (e.g. a base URL, or empty
 * whitespace) with a message before that request is ever made, not to
 * pretend this server can tell a live key from a dead one.
 */
function looksLikeAnOpenAIKey(value: string): boolean {
  return value.startsWith("sk-") && value.length >= 20;
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "invalid_request", message: "expected a JSON body" },
      { status: 400 },
    );
  }

  const { provider, model, apiKey } = (body ?? {}) as Record<string, unknown>;

  if (provider !== "local" && provider !== "openai") {
    return NextResponse.json(
      { error: "invalid_request", message: 'provider must be "local" or "openai"' },
      { status: 400 },
    );
  }
  if (typeof model !== "string" || model.trim() === "") {
    return NextResponse.json(
      { error: "invalid_request", message: "model is required" },
      { status: 400 },
    );
  }

  let config: Parameters<typeof assistantSessions.set>[1];
  if (provider === "openai") {
    if (typeof apiKey !== "string" || !looksLikeAnOpenAIKey(apiKey.trim())) {
      // The message names the shape expected, never the value received —
      // the same rule applied to a malformed ASSISTANT_MODEL_BASE_URL in
      // lib/assistantModel.ts: a bad credential is exactly the case where
      // echoing the input back is the mistake.
      return NextResponse.json(
        {
          error: "invalid_request",
          message: 'an OpenAI API key (starting "sk-") is required for provider "openai"',
        },
        { status: 400 },
      );
    }
    config = { provider: "openai", model: model.trim(), apiKey: apiKey.trim() };
  } else {
    config = { provider: "local", model: model.trim() };
  }

  let sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  if (!sessionId) sessionId = newSessionId();
  assistantSessions.set(sessionId, config);

  // The response confirms what was saved in the same shape GET returns —
  // provider, model, and only whether a key is set. The key itself is never
  // in a response body, on this route or any other.
  const response = NextResponse.json(toStatus(config));
  response.cookies.set(SESSION_COOKIE, sessionId, secureSessionCookieOptions(request, SESSION_TTL_MS));
  return response;
}

export async function DELETE(request: NextRequest) {
  const sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  assistantSessions.clear(sessionId);
  const response = NextResponse.json({ active: false });
  // Expire the cookie immediately rather than merely deleting server-side
  // state — a browser that still sends the old id should not be quietly
  // reissued a fresh empty session under the same value.
  response.cookies.set(SESSION_COOKIE, "", { ...secureSessionCookieOptions(request, 0), maxAge: 0 });
  return response;
}

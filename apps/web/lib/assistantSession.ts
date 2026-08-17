import "server-only";
import { randomUUID } from "node:crypto";
import type { AssistantSessionStatus } from "./assistantSessionTypes.ts";

export type { AssistantSessionStatus } from "./assistantSessionTypes.ts";

/**
 * Per-browser-session assistant configuration: which provider, which model,
 * and — only for `openai` — a key the visitor typed in themselves.
 *
 * This is the answer to the shared-machine failure mode a naive
 * implementation falls into: a single server-side variable holding "the"
 * API key means the last person who typed one silently spends *everyone's*
 * budget on their key, because every visitor's request reads the same
 * value. Nothing here is keyed that way. Every config is stored behind a
 * random session id that only one browser holds — see `SESSION_COOKIE` in
 * the route handlers — so two visitors' sessions cannot see or overwrite
 * each other no matter how the store itself is implemented underneath.
 *
 * What this module does not do, on purpose:
 *   - write the key to disk, a database, or a log — the store is a plain
 *     in-memory Map, so a server restart empties it. That is the whole of
 *     "no persistence": there is no code path here that could persist it.
 *   - return the key from any function that isn't directly building the
 *     OpenAI client that needs it. `getSession()` returns the whole config
 *     including the key because the caller (the CopilotKit route) needs it
 *     to make the request; nothing downstream of that is allowed to echo
 *     it — see the route handlers' own comments for where that boundary is
 *     enforced.
 */

export type AssistantSessionConfig =
  | { provider: "local"; model: string }
  | { provider: "openai"; model: string; apiKey: string };

export const SESSION_COOKIE = "assistant_session";

/** Idle sessions are dropped even within one server lifetime — a key typed
 * in a browser tab left open for days should not still be usable a week
 * later just because the process never restarted. */
export const SESSION_TTL_MS = 12 * 60 * 60 * 1000; // 12 hours

type StoredSession = AssistantSessionConfig & { lastUsedAt: number };

/**
 * A fresh, isolated store. The route handlers share one instance
 * (`assistantSessions` below); tests create their own so that one test's
 * sessions can never be seen by another's, which is the same isolation
 * property this store exists to give real visitors.
 */
export function createAssistantSessionStore(ttlMs: number = SESSION_TTL_MS) {
  const sessions = new Map<string, StoredSession>();

  function sweep(now: number): void {
    for (const [id, session] of sessions) {
      if (now - session.lastUsedAt > ttlMs) sessions.delete(id);
    }
  }

  return {
    /** The config for one session id, or null if there isn't one (or it expired). */
    get(sessionId: string | undefined | null): AssistantSessionConfig | null {
      if (!sessionId) return null;
      const now = Date.now();
      sweep(now);
      const found = sessions.get(sessionId);
      if (!found) return null;
      found.lastUsedAt = now;
      const { lastUsedAt: _lastUsedAt, ...config } = found;
      return config;
    },

    /** Replaces whatever this session id had. A new session id gets a fresh entry. */
    set(sessionId: string, config: AssistantSessionConfig): void {
      sweep(Date.now());
      sessions.set(sessionId, { ...config, lastUsedAt: Date.now() });
    },

    /** Forgets a session's config entirely — the "clear my key" action. */
    clear(sessionId: string | undefined | null): void {
      if (sessionId) sessions.delete(sessionId);
    },

    /** For tests only: how many sessions are currently held. */
    size(): number {
      return sessions.size;
    },
  };
}

/** The one store the running server process actually uses. */
export const assistantSessions = createAssistantSessionStore();

export function newSessionId(): string {
  return randomUUID();
}

/** Never includes the key — this is the only shape a response body may carry. */
export function toStatus(config: AssistantSessionConfig | null): AssistantSessionStatus {
  if (!config) return { active: false };
  return {
    active: true,
    provider: config.provider,
    model: config.model,
    hasApiKey: config.provider === "openai",
  };
}

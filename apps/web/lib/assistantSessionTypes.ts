/**
 * Types shared between `lib/assistantSession.ts` (server-only) and the
 * Client Components that render a session's status. Separate for the same
 * reason `lib/gatewayTypes.ts` is separate from `lib/gateway.ts`: importing
 * a `server-only`-marked module from client code is a build error by
 * design, and that guard should stay meaningful — a types-only file next to
 * it is what lets a component name the shape without needing the module
 * that enforces the boundary to make an exception for it.
 */

export type AssistantSessionStatus =
  | { active: false }
  | { active: true; provider: "local" | "openai"; model: string; hasApiKey: boolean };

export type AssistantModelsResponse = {
  models: string[];
  defaultModel: string | null;
  reason?: string;
  warning?: string;
};

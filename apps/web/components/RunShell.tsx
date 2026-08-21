import { cookies } from "next/headers";
import AppShell, { type ShellRun } from "@/components/AppShell";
import { describeAssistantModel } from "@/lib/assistantModel";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";
import { controllerConfigured } from "@/lib/controller";
import { SESSION_COOKIE, assistantSessions } from "@/lib/assistantSession";

/**
 * Server-side wrapper that reads the assistant's configuration and hands the
 * shell only what a browser may see.
 *
 * `describeAssistantModel()` returns the model name and a credential-stripped
 * endpoint, never the API key, so nothing secret crosses into the client
 * component tree. `controllerConfigured()` returns a boolean, never the
 * controller's address. Every page renders inside this.
 *
 * The assistant mounted in the shell is always the **read-only** one, on every
 * page including `/analysis/new`. The intake assistant lives inside the intake
 * page's own panel with its own action set — see `app/api/copilotkit/route.ts`
 * for why the two sets are never merged.
 */

/**
 * The model that would actually answer this request, and where it came from.
 *
 * The header used to render `describeAssistantModel()` unconditionally, which
 * reads the environment. A visitor who set their own OpenAI key in the
 * settings panel therefore saw the *server's* default model in the header
 * afterwards, and the only prominent indicator on the page went on saying
 * `gpt-oss:120b`. The setting had taken effect — `/api/copilotkit` resolves
 * the session per request and always did — but nothing on screen said so, so
 * it looked exactly like nothing had happened.
 *
 * Resolved here rather than in the client because the session store is
 * server-side memory keyed by an `httpOnly` cookie, which page JavaScript
 * cannot read by design. Only the model name and its origin cross into the
 * tree; the key never leaves this function's scope, and is not read from the
 * store at all.
 */
async function effectiveModel() {
  const fallback = describeAssistantModel();
  const sessionId = (await cookies()).get(SESSION_COOKIE)?.value;
  const session = assistantSessions.get(sessionId);

  if (session?.provider === "openai") {
    return {
      configured: true as const,
      model: session.model,
      endpoint: "api.openai.com",
      origin: "your OpenAI key" as const,
      reason: null,
    };
  }
  if (session?.provider === "local" && fallback.configured) {
    return {
      configured: true as const,
      model: session.model,
      endpoint: fallback.endpoint,
      origin: "your choice on the lab endpoint" as const,
      reason: null,
    };
  }
  return fallback.configured
    ? {
        configured: true as const,
        model: fallback.model,
        endpoint: fallback.endpoint,
        origin: null,
        reason: null,
      }
    : { configured: false as const, model: null, endpoint: null, origin: null, reason: fallback.reason };
}

export default async function RunShell({
  run,
  instructions = READ_ONLY_INSTRUCTIONS,
  assistantDefaultOpen,
  assistantTitle,
  assistantInitialMessage,
  assistantSuggestions,
  children,
}: {
  run: ShellRun;
  instructions?: string;
  assistantDefaultOpen?: boolean;
  assistantTitle?: string;
  assistantInitialMessage?: string;
  assistantSuggestions?: string[];
  children: React.ReactNode;
}) {
  const model = await effectiveModel();
  return (
    <AppShell
      run={run}
      assistantConfigured={model.configured}
      assistantReason={model.configured ? null : model.reason}
      assistantModel={model.model}
      assistantEndpoint={model.endpoint}
      assistantModelOrigin={model.origin}
      instructions={instructions}
      assistantDefaultOpen={assistantDefaultOpen}
      assistantTitle={assistantTitle}
      assistantInitialMessage={assistantInitialMessage}
      assistantSuggestions={assistantSuggestions}
      canStartAnalyses={controllerConfigured()}
    >
      {children}
    </AppShell>
  );
}

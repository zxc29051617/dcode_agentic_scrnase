import { NextRequest, NextResponse } from "next/server";
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  OpenAIAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import OpenAI from "openai";
import { READ_ONLY_ACTIONS } from "@/lib/assistantActions";
import { getAssistantModelConfig, scrubSecrets } from "@/lib/assistantModel";

/**
 * The CopilotKit runtime endpoint.
 *
 * The five actions come from `lib/assistantActions.ts` so that the runtime
 * and the conversation test share one definition. The model configuration
 * comes from `lib/assistantModel.ts`, which reads the server environment and
 * never lets the API key out of that module — this file passes it straight
 * into the OpenAI client and holds no other copy of it.
 */

// `CopilotRuntime`'s `actions` callback infers one `Action<P>` type parameter
// for the whole returned array, so a zero-argument action (`list_runs`) and a
// one-argument action (everything else) cannot both type-check under a single
// inferred `P` — TypeScript unifies the *first* element's parameter shape and
// then rejects every other element's handler signature against it. The
// explicit `any[]` return type opts each element back into its own literal
// parameter/handler pairing, which is what actually executes; nothing here
// changes at runtime, only what the compiler is asked to unify.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const runtime = new CopilotRuntime({ actions: (): any[] => READ_ONLY_ACTIONS });

/** Upper bound on one model turn. See the note where the client is built. */
const MODEL_TIMEOUT_MS = 120_000;

/**
 * A real model when one is configured, `ExperimentalEmptyAdapter` when not.
 *
 * The fallback is kept deliberately: it lets every page, every action and the
 * whole request path run with no model and no credential at all. What it must
 * never do is look like a working assistant, so the assistant page asks
 * `describeAssistantModel()` and renders the unconfigured state instead of a
 * chat box — see `app/runs/[id]/assistant/page.tsx`.
 */
function serviceAdapter() {
  const config = getAssistantModelConfig();
  if (!config.configured) {
    return new ExperimentalEmptyAdapter();
  }
  // A large model on a shared GPU is legitimately slow — gpt-oss:120b takes
  // tens of seconds for a tool-calling turn — so this is generous. What it
  // must not be is absent: without a bound, an endpoint that accepts the
  // connection and then stalls leaves the chat spinning with no error and
  // nothing in the log to say why.
  const openai = new OpenAI({
    baseURL: config.baseURL,
    apiKey: config.apiKey,
    timeout: MODEL_TIMEOUT_MS,
    maxRetries: 1,
  });
  return new OpenAIAdapter({ openai, model: config.model });
}

export async function POST(req: NextRequest) {
  const config = getAssistantModelConfig();
  try {
    const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
      runtime,
      serviceAdapter: serviceAdapter(),
      endpoint: "/api/copilotkit",
    });
    return await handleRequest(req);
  } catch (error) {
    // An unreachable or wrong endpoint surfaces here. The message is scrubbed
    // before it goes anywhere, because an OpenAI client error can quote the
    // request it failed on and a misconfigured base URL can carry credentials
    // in the URL itself. The scrubbed text is what is returned *and* what is
    // logged — there is no unscrubbed path.
    const secrets = config.configured ? [config.apiKey, config.baseURL] : [];
    const message = scrubSecrets(
      error instanceof Error ? error.message : String(error),
      secrets,
    );
    console.error("[copilotkit] assistant request failed:", message);
    return NextResponse.json(
      { error: "assistant_unavailable", message },
      { status: 502 },
    );
  }
}

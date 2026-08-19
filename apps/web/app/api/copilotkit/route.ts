import { NextRequest, NextResponse } from "next/server";
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  OpenAIAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import OpenAI from "openai";
import { READ_ONLY_ACTIONS } from "@/lib/assistantActions";
import { INTAKE_ACTIONS } from "@/lib/intakeActions";
import { GATE_ADVISOR_ACTIONS } from "@/lib/gateAdvisorActions";
import { getAssistantModelConfig, scrubSecrets, type AssistantModelConfig } from "@/lib/assistantModel";
import { SESSION_COOKIE, assistantSessions } from "@/lib/assistantSession";

/**
 * The CopilotKit runtime endpoint.
 *
 * The actions come from `lib/assistantActions.ts` and `lib/intakeActions.ts`
 * so that the runtime and the conversation tests share one definition. The
 * model configuration comes from `resolveConfig()` below, which layers a
 * visitor's own session (chosen local model, or their own OpenAI key) over the
 * server's env default — never the other way around, and never a value shared
 * across sessions. See `lib/assistantSession.ts` for the isolation guarantee
 * this depends on.
 *
 * ## Two assistants, two action sets, never merged
 *
 * `?mode=intake` gives the four intake actions, `?mode=gate` the three an
 * advisor needs beside a paused decision, and anything else the five read-only
 * ones. They are separate on purpose and the union is never offered:
 *
 * - The run assistant explains recorded results. Handing it
 *   `prepare_analysis_request` would let a conversation about an existing run
 *   quietly produce a request for a new one.
 * - The intake assistant prepares a request. Handing it `get_report` would not
 *   be dangerous, but it would blur what the thing is, and the read-only
 *   assistant's guarantee — "no function that writes was put within its reach"
 *   — is only legible if the two sets stay distinct.
 *
 * - The gate advisor argues for an option at a paused gate. Everything it can
 *   reach is a GET; it reads the candidates the person is looking at and the
 *   evidence that bears on them, and that is all.
 *
 * No set contains a confirm or a gate answer. There is no action anywhere in
 * this app that can start a run or answer `accept`, `revise` or `stop`.
 */

//: Which action set a request gets. Read from the URL rather than a header so
//: that it is visible in a network log, and defaulting to read-only so a
//: missing parameter is the safe case rather than the powerful one.
function actionsFor(request: NextRequest) {
  const mode = request.nextUrl.searchParams.get("mode");
  if (mode === "intake") return INTAKE_ACTIONS;
  if (mode === "gate") return GATE_ADVISOR_ACTIONS;
  return READ_ONLY_ACTIONS;
}

// `CopilotRuntime`'s `actions` callback infers one `Action<P>` type parameter
// for the whole returned array, so a zero-argument action (`list_runs`) and a
// one-argument action (everything else) cannot both type-check under a single
// inferred `P` — TypeScript unifies the *first* element's parameter shape and
// then rejects every other element's handler signature against it. The
// explicit `any[]` return type opts each element back into its own literal
// parameter/handler pairing, which is what actually executes; nothing here
// changes at runtime, only what the compiler is asked to unify.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function runtimeFor(request: NextRequest) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return new CopilotRuntime({ actions: (): any[] => actionsFor(request) });
}

/** Upper bound on one model turn. See the note where the client is built. */
const MODEL_TIMEOUT_MS = 120_000;

const OPENAI_BASE_URL = "https://api.openai.com/v1";

/**
 * Which model configuration this specific request should use.
 *
 * Three cases, in order:
 *   1. This browser's session says "use my own OpenAI key" — use it, talking
 *      to api.openai.com directly. The key came from this one session's
 *      entry in `assistantSessions`, looked up by the session id in this
 *      request's own cookie; no other request, from any other visitor,
 *      shares that lookup.
 *   2. This session picked a model from the local endpoint's own list — use
 *      the server's configured endpoint and key, but that session's chosen
 *      model name instead of the env default.
 *   3. No session, or an expired one — the env default, exactly as before
 *      this feature existed.
 */
function resolveConfig(request: NextRequest): AssistantModelConfig {
  const envConfig = getAssistantModelConfig();
  const sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  const session = assistantSessions.get(sessionId);

  if (session?.provider === "openai") {
    return {
      configured: true,
      baseURL: OPENAI_BASE_URL,
      apiKey: session.apiKey,
      model: session.model,
      displayEndpoint: "api.openai.com",
    };
  }
  if (session?.provider === "local" && envConfig.configured) {
    return { ...envConfig, model: session.model };
  }
  return envConfig;
}

/**
 * A real model when one is configured, `ExperimentalEmptyAdapter` when not.
 *
 * The fallback is kept deliberately: it lets every page, every action and the
 * whole request path run with no model and no credential at all. What it must
 * never do is look like a working assistant, so the assistant page asks
 * `describeAssistantModel()` and renders the unconfigured state instead of a
 * chat box — see `app/runs/[id]/assistant/page.tsx`.
 */
function serviceAdapter(config: AssistantModelConfig) {
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
  const config = resolveConfig(req);
  try {
    const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
      runtime: runtimeFor(req),
      serviceAdapter: serviceAdapter(config),
      endpoint: "/api/copilotkit",
    });
    return await handleRequest(req);
  } catch (error) {
    // An unreachable or wrong endpoint surfaces here. The message is scrubbed
    // before it goes anywhere, because an OpenAI client error can quote the
    // request it failed on and a misconfigured base URL — or a visitor's own
    // key, if their session supplied one — can appear in it. The scrubbed
    // text is what is returned *and* what is logged — there is no
    // unscrubbed path.
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

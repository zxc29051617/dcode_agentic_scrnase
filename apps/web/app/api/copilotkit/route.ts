import { NextRequest } from "next/server";
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  OpenAIAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import OpenAI from "openai";
import {
  getProvenance,
  getReport,
  getRunSnapshot,
  getStepRecords,
  listRuns,
} from "@/lib/gateway";

/**
 * Every action here does exactly one thing: call a read-only gateway function
 * from lib/gateway.ts and return what it returned. None of them accepts a
 * config value, a threshold, or a decision — there is nothing here for an
 * assistant to be tricked into "revising" or "accepting", because the
 * capability to do either was never wired in. See
 * docs/deep_agents_architecture.md §5.1/§6.3 for the same boundary applied to
 * the Deep Agents reviewer layer this UI will eventually also surface.
 */
const RUN_ID_PARAM = [
  { name: "run_id", type: "string" as const, description: "the scientific run id", required: true as const },
];

// `CopilotRuntime`'s `actions` callback infers one `Action<P>` type parameter
// for the whole returned array, so a zero-argument action (`list_runs`) and a
// one-argument action (everything else) cannot both type-check under a single
// inferred `P` — TypeScript unifies the *first* element's parameter shape and
// then rejects every other element's handler signature against it. The
// explicit `Action<any>[]` return type below opts each element back into its
// own literal parameter/handler pairing, which is what actually executes;
// nothing here changes at runtime, only what the compiler is asked to unify.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const runtime = new CopilotRuntime({
  actions: (): any[] => [
    {
      name: "list_runs",
      description: "List every scientific run the gateway can see, with its status.",
      parameters: [],
      handler: async () => listRuns(),
    },
    {
      name: "get_run_snapshot",
      description: "Get one run's status, step list and any pending human gate.",
      parameters: RUN_ID_PARAM,
      handler: async ({ run_id }: { run_id: string }) => {
        const snapshot = await getRunSnapshot(run_id);
        return snapshot ?? { error: `no run ${run_id}` };
      },
    },
    {
      name: "get_step_record",
      description: "Get every step's status, judge verdict and output summary for one run.",
      parameters: RUN_ID_PARAM,
      handler: async ({ run_id }: { run_id: string }) => {
        const steps = await getStepRecords(run_id);
        return steps ?? { error: `no run ${run_id}` };
      },
    },
    {
      name: "get_report",
      description: "Get the saved report for one run, if it has been produced.",
      parameters: RUN_ID_PARAM,
      handler: async ({ run_id }: { run_id: string }) => {
        const report = await getReport(run_id);
        return report ?? { error: `no run ${run_id}` };
      },
    },
    {
      name: "get_provenance",
      description: "Get the redacted provenance record (config, packages, judge sessions) for one run.",
      parameters: RUN_ID_PARAM,
      handler: async ({ run_id }: { run_id: string }) => {
        const provenance = await getProvenance(run_id);
        return provenance ?? { error: `no run ${run_id}` };
      },
    },
  ],
});

/**
 * No model endpoint or API key was available to wire in for this phase — no
 * local Ollama, no project `.env`, nothing provided. `ASSISTANT_MODEL_*` are
 * read only here, server-side; they are never exposed to the browser.
 * Unset, this falls back to `ExperimentalEmptyAdapter`, CopilotKit's own
 * documented adapter for exactly this case, so the actions above and the chat
 * UI below still exercise the real request/response path end to end against
 * real gateway data — with no model call and no key of any kind.
 */
function serviceAdapter() {
  const baseURL = process.env.ASSISTANT_MODEL_BASE_URL;
  const apiKey = process.env.ASSISTANT_MODEL_API_KEY;
  if (baseURL) {
    const openai = new OpenAI({ baseURL, apiKey: apiKey || "not-needed" });
    return new OpenAIAdapter({ openai });
  }
  return new ExperimentalEmptyAdapter();
}

export async function POST(req: NextRequest) {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: serviceAdapter(),
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
}

import "server-only";

/**
 * The only place in this app that knows the analysis controller's URL.
 *
 * `server-only` makes importing this from a Client Component a build error,
 * which is the enforcement — the browser never learns
 * `ANALYSIS_CONTROLLER_URL` and never talks to the controller directly. Every
 * page, every route handler and every intake action goes through here.
 *
 * Deliberately a second module from `lib/gateway.ts`, pointing at a second
 * service. The gateway is GET-only and stays that way; this one can cause a
 * run to exist. Keeping them separate in the app mirrors keeping them separate
 * on the network, so a reader can see which imports do which by name.
 */

import type {
  CatalogView,
  SpeciesCatalogView,
  ConfirmResponse,
  DatasetOption,
  DecisionResponse,
  GateState,
  PreviewResponse,
  RequestStatusView,
  StudyDesignOption,
} from "./controllerTypes.ts";

export type {
  AnalysisRequest,
  AnalysisSettings,
  CatalogView,
  SpeciesCatalogView,
  SpeciesProfileView,
  ConfirmResponse,
  DatasetOption,
  DecisionResponse,
  ExecutionPlan,
  GateState,
  MissingQuestion,
  PreviewResponse,
  RequestStatus,
  RequestStatusView,
  StudyDesignOption,
} from "./controllerTypes.ts";

function controllerUrl(): string {
  const url = process.env.ANALYSIS_CONTROLLER_URL;
  if (!url) {
    throw new Error("ANALYSIS_CONTROLLER_URL is not set. See .env.local.example.");
  }
  return url;
}

/** Whether the write side is configured at all. Pages render a clear
 *  unconfigured state rather than a broken form when it is not. */
export function controllerConfigured(): boolean {
  return Boolean(process.env.ANALYSIS_CONTROLLER_URL);
}

/**
 * How long a request will wait. The controller validates and writes a SQLite
 * row; a slow answer means something is wrong, not that something is big.
 */
const CONTROLLER_TIMEOUT_MS = 15_000;

export class ControllerError extends Error {
  // Written out rather than declared as constructor parameter properties:
  // `npm run test:unit` runs Node's strip-only TypeScript mode, which refuses
  // that syntax, and this module is on the import path of the intake tests.
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "ControllerError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * One call to the controller.
 *
 * The error path is the interesting part. A controller error body can quote
 * the request it failed on, and a misconfigured `ANALYSIS_CONTROLLER_URL` can
 * appear in a fetch failure — so what leaves this function is the controller's
 * own `detail` field, which is written for an operator to read, and never the
 * raw transport error text.
 */
async function call<T>(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${controllerUrl()}${path}`, {
      method: init?.method ?? "GET",
      cache: "no-store",
      headers: init?.body ? { "content-type": "application/json" } : undefined,
      body: init?.body ? JSON.stringify(init.body) : undefined,
      signal: AbortSignal.timeout(CONTROLLER_TIMEOUT_MS),
    });
  } catch (error) {
    const reason =
      error instanceof Error && error.name === "TimeoutError"
        ? `did not respond within ${CONTROLLER_TIMEOUT_MS / 1000}s`
        : "could not be reached";
    // The address is not repeated into the message: it is a server-side value
    // and this string can reach a browser.
    throw new ControllerError(
      503,
      `the analysis controller ${reason}. Is services/controller running?`,
    );
  }

  const text = await response.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    const detail =
      parsed && typeof parsed === "object" && "detail" in (parsed as Record<string, unknown>)
        ? (parsed as Record<string, unknown>).detail
        : `the analysis controller returned ${response.status}`;
    throw new ControllerError(response.status, detail);
  }
  return parsed as T;
}

export type PreviewInput = {
  request_id?: string | null;
  conversation_id?: string | null;
  project?: string | null;
  species?: string | null;
  research_question?: string | null;
  input_ref?: string | null;
  /** Untrusted text from a conversation. The controller validates it against
   *  its own allowlist and replaces it with a reference; it is never
   *  forwarded to a worker as given. */
  input_path?: string | null;
  input_kind_hint?: string | null;
  study_design_ref?: string | null;
  analysis?: Record<string, unknown>;
};

export async function previewAnalysisRequest(input: PreviewInput): Promise<PreviewResponse> {
  return call<PreviewResponse>("/v1/analysis-requests/preview", {
    method: "POST",
    body: input,
  });
}

export async function getAnalysisRequest(requestId: string) {
  return call<{ request: unknown; job: unknown; run: unknown; decisions: unknown[] }>(
    `/v1/analysis-requests/${encodeURIComponent(requestId)}`,
  );
}

export async function getAnalysisRequestStatus(requestId: string): Promise<RequestStatusView> {
  return call<RequestStatusView>(
    `/v1/analysis-requests/${encodeURIComponent(requestId)}/status`,
  );
}

/**
 * Record a human confirmation and queue the scientific job.
 *
 * Called from `app/api/analysis-requests/[requestId]/confirm/route.ts`, which
 * is reached by a button. There is deliberately no CopilotKit action that
 * reaches this function — see `lib/intakeActions.ts`.
 */
export async function confirmAnalysisRequest(
  requestId: string,
  body: { config_digest: string; operator_id: string; rationale?: string },
): Promise<ConfirmResponse> {
  return call<ConfirmResponse>(
    `/v1/analysis-requests/${encodeURIComponent(requestId)}/confirm`,
    { method: "POST", body },
  );
}

export async function getGateState(runId: string): Promise<GateState> {
  return call<GateState>(`/v1/scientific-runs/${encodeURIComponent(runId)}/gate`);
}

/**
 * Submit one human answer to one pending gate.
 *
 * `overrides` are sent as the operator typed them. They are *not* converted
 * here: `src/registry.py::coerce_overrides` on the controller is the only
 * place that decides what a value means, so the browser and the terminal
 * cannot drift into accepting different things.
 */
export async function submitGateDecision(
  runId: string,
  gateId: string,
  body: {
    decision: "accept" | "revise" | "stop";
    operator_id: string;
    expected_generation: number;
    rationale?: string;
    overrides?: Record<string, unknown>;
  },
): Promise<DecisionResponse> {
  return call<DecisionResponse>(
    `/v1/scientific-runs/${encodeURIComponent(runId)}/gates/${encodeURIComponent(gateId)}/decision`,
    { method: "POST", body },
  );
}

export async function listCatalog(): Promise<CatalogView> {
  return call<CatalogView>("/v1/datasets");
}

export async function listSpecies(): Promise<SpeciesCatalogView> {
  return call<SpeciesCatalogView>("/v1/species");
}

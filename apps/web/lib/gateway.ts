import "server-only";

/**
 * The only place in this app that knows the gateway's URL. `server-only`
 * makes importing this file from a Client Component a build error, which is
 * the enforcement — not a comment asking nobody to do it. Every page and
 * every CopilotKit action reads run data through the functions here, never
 * through a browser-side fetch to GATEWAY_URL.
 */

function gatewayUrl(): string {
  const url = process.env.GATEWAY_URL;
  if (!url) {
    throw new Error("GATEWAY_URL is not set. See .env.local.example.");
  }
  return url;
}

/**
 * How long a page will wait for the gateway before giving up.
 *
 * The gateway only reads JSON files off local disk, so a slow answer means
 * something is wrong rather than something is big. Without a bound, a
 * gateway that accepts a connection and then stalls hangs the page render
 * forever, and the browser shows a blank screen with nothing to explain it.
 */
const GATEWAY_TIMEOUT_MS = 10_000;

async function getJson<T>(path: string): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(`${gatewayUrl()}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
    });
  } catch (error) {
    // A timeout and a refused connection both arrive here. Say which, and
    // say where, because "failed to fetch" on a blank page tells nobody
    // whether the gateway is down or simply not running yet.
    const reason =
      error instanceof Error && error.name === "TimeoutError"
        ? `did not respond within ${GATEWAY_TIMEOUT_MS / 1000}s`
        : `could not be reached (${error instanceof Error ? error.message : String(error)})`;
    throw new Error(`gateway ${path} ${reason}. Is services/gateway running on ${gatewayUrl()}?`);
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`gateway ${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}

// The response shapes live in `lib/gatewayTypes.ts` so a Client Component can
// name them without importing this server-only module. Re-exported here so
// existing server-side imports keep working from one place.
export type {
  RunSummary,
  StepEntry,
  PendingGate,
  RunSnapshot,
  StepRecord,
  ReportView,
  Provenance,
  ArtifactEntry,
} from "./gatewayTypes.ts";
import type {
  StepTimings,
  RunSummary,
  RunSnapshot,
  StepRecord,
  ReportView,
  Provenance,
  ArtifactEntry,
} from "./gatewayTypes.ts";

/**
 * The gateway URL for one artifact's bytes.
 *
 * Server-side only — this module is `server-only`, so a browser can never
 * call this and never learns the address it builds. Pages and the proxy route
 * hand the browser `/api/artifacts/...` instead.
 */
export function artifactUrl(runId: string, artifactId: string, download = false): string {
  const base = `${gatewayUrl()}/v1/scientific-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`;
  return download ? `${base}?download=true` : base;
}

export async function listArtifacts(runId: string): Promise<ArtifactEntry[] | null> {
  return getJson<ArtifactEntry[]>(`/v1/scientific-runs/${encodeURIComponent(runId)}/artifacts`);
}

export async function listRuns(): Promise<RunSummary[]> {
  return (await getJson<RunSummary[]>("/v1/scientific-runs")) ?? [];
}

export async function getRunSnapshot(runId: string): Promise<RunSnapshot | null> {
  return getJson<RunSnapshot>(`/v1/scientific-runs/${encodeURIComponent(runId)}`);
}

export async function getStepRecords(runId: string): Promise<StepRecord[] | null> {
  return getJson<StepRecord[]>(`/v1/scientific-runs/${encodeURIComponent(runId)}/steps`);
}

export async function getReport(runId: string): Promise<ReportView | null> {
  return getJson<ReportView>(`/v1/scientific-runs/${encodeURIComponent(runId)}/report`);
}

export async function getStepTimings(): Promise<StepTimings | null> {
  return getJson<StepTimings>("/v1/step-timings");
}

export async function getProvenance(runId: string): Promise<Provenance | null> {
  return getJson<Provenance>(`/v1/scientific-runs/${encodeURIComponent(runId)}/provenance`);
}

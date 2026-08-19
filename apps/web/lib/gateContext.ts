import "server-only";
import { getGateState } from "./controller.ts";
import { getRunSnapshot, getStepRecords } from "./gateway.ts";
import { candidatesFor } from "./gateCandidates.ts";

/**
 * Everything an advisor needs to argue for one option over another at a gate.
 *
 * Assembled here rather than handed over raw, for two reasons.
 *
 * **It has to fit in a conversation.** The recorded evidence for one gate is
 * sixty-one models with descriptions plus a marker table of thirteen clusters
 * with effect sizes and adjusted p-values for every gene. Pasted whole that is
 * most of a context window spent on numbers nobody asked about, and the model
 * reads it worse than a projection that leads with the decision.
 *
 * **It has to be the same facts the person is looking at.** The candidates come
 * from `candidatesFor`, the same function the picker renders from, so the
 * advisor cannot recommend an option that is not on screen or miss one that is.
 *
 * Read-only throughout: two GETs to the gateway and one to the controller.
 * Nothing here can answer a gate, and no function it calls can.
 */

/** Marker genes per cluster, names only. */
const MARKERS_PER_CLUSTER = 6;

/** How many clusters' markers travel. Enough to characterise the tissue. */
const MAX_CLUSTERS = 15;

export type GateBriefing = {
  found: boolean;
  reason?: string;
  run_id?: string;
  /** What the run stopped to ask. */
  question?: {
    step: string;
    parameter: string | null;
    verdict: string | null;
    reasons: string[];
    /** How many gates this run has opened. A decision must name it. */
    generation: number;
  };
  /** The options, exactly as the picker shows them. */
  options?: {
    value: string;
    description: string | null;
    /** `true` ready now, `false` needs downloading, `null` not applicable. */
    available_locally: boolean | null;
  }[];
  /** What is known about this data, which is what makes one option better. */
  context?: {
    species: string | null;
    n_clusters: number | null;
    n_cells: number | null;
    /** `{ "0": ["S100A8", "LYZ", …] }` — the signature of each cluster. */
    top_markers_by_cluster: Record<string, string[]>;
  };
};

function markerNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, MARKERS_PER_CLUSTER)
    .map((entry) =>
      entry && typeof entry === "object" && typeof (entry as { gene?: unknown }).gene === "string"
        ? (entry as { gene: string }).gene
        : null,
    )
    .filter((g): g is string => Boolean(g));
}

export async function gateBriefing(runId: string): Promise<GateBriefing> {
  let gate;
  try {
    gate = await getGateState(runId);
  } catch {
    return { found: false, reason: `no run ${runId}, or the controller is unreachable` };
  }
  if (!gate.pending_gate) {
    return {
      found: false,
      reason: `run ${runId} is not waiting at a gate (status ${gate.status}); there is nothing to advise on`,
    };
  }

  const pending = gate.pending_gate;
  // One parameter per gate in this pipeline today. Named rather than assumed,
  // so a gate that offers none produces a briefing that says so instead of
  // inviting the model to invent a choice.
  const parameter = pending.revisable?.[0] ?? null;
  const enumerated = parameter ? candidatesFor(parameter, pending.evidence) : null;

  const [snapshot, steps] = await Promise.all([
    getRunSnapshot(runId).catch(() => null),
    getStepRecords(runId).catch(() => null),
  ]);

  const markers: Record<string, string[]> = {};
  const findMarkers = steps?.find((s) => s.step === "find_markers");
  const table = (findMarkers?.settings ?? {}) as Record<string, unknown>;
  const top = table.top_markers;
  if (top && typeof top === "object" && !Array.isArray(top)) {
    for (const [cluster, genes] of Object.entries(top).slice(0, MAX_CLUSTERS)) {
      const names = markerNames(genes);
      if (names.length) markers[cluster] = names;
    }
  }

  return {
    found: true,
    run_id: runId,
    question: {
      step: pending.step,
      parameter,
      verdict: pending.verdict,
      reasons: pending.reasons ?? [],
      generation: gate.generation,
    },
    options: (enumerated?.candidates ?? []).map((c) => ({
      value: c.value,
      description: c.description,
      available_locally: c.local,
    })),
    context: {
      species: snapshot?.species ?? null,
      n_clusters: snapshot?.clusters ?? null,
      n_cells: snapshot?.cells ?? null,
      top_markers_by_cluster: markers,
    },
  };
}

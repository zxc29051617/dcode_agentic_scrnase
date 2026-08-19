/**
 * Turning a gate's evidence into the options a person picks from.
 *
 * Two steps in this pipeline stop rather than guess, and both stop for the same
 * reason: a wrong choice does not fail, it succeeds and returns confident wrong
 * labels. `annotate_cells` reports 61 CellTypist models with their
 * descriptions; `cross_check_annotation` reports the tissues its marker
 * database covers. Both write that list into the gate's evidence precisely so
 * somebody can choose from it — `skills/annotate_cells/annotate_cells.py` says
 * as much: "That evidence is also exactly what an advisor model needs in order
 * to argue for one model over another."
 *
 * The app was rendering that list as a wall of raw JSON and then, separately,
 * an empty text box. So the work of reading sixty-one entries, remembering a
 * filename and retyping it fell on the person — for a decision the system had
 * already assembled every option for.
 *
 * This module is the join. It reads only what the executor recorded, and it
 * invents nothing: a parameter with no candidates in evidence returns `null`
 * and the caller falls back to a free-text field, which is the honest
 * behaviour for a value nobody enumerated.
 */

export type GateCandidate = {
  value: string;
  description: string | null;
  /**
   * Whether this option can run without fetching anything first.
   *
   * `null` where the concept does not apply — a marker-database tissue is not
   * downloaded, it is simply covered or not. `false` must never be presented
   * as ready: choosing an absent model is a decision to wait for a download,
   * and finding that out after confirming is the failure this flag prevents.
   */
  local: boolean | null;
};

export type GateCandidates = {
  parameter: string;
  candidates: GateCandidate[];
  /** Set when availability is meaningful, so the UI can group by it. */
  hasLocality: boolean;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * The options `parameter` offers, or `null` if this gate enumerated none.
 *
 * Shapes are read defensively throughout. Evidence is written by a skill and
 * projected through two services; a field that is missing, renamed or of an
 * unexpected type has to degrade to "no candidates" — which restores the text
 * box — rather than throwing inside the one control a person needs to answer a
 * paused run with.
 */
export function candidatesFor(
  parameter: string,
  evidence: Record<string, unknown> | null | undefined,
): GateCandidates | null {
  if (!evidence) return null;

  if (parameter === "celltypist_model") {
    const models = asRecord(evidence.models);
    if (!models) return null;

    const downloaded = new Set(
      (Array.isArray(models.downloaded) ? models.downloaded : [])
        .filter((m): m is string => typeof m === "string"),
    );
    const available = Array.isArray(models.available) ? models.available : [];

    const candidates: GateCandidate[] = [];
    for (const entry of available) {
      const row = asRecord(entry);
      const value = typeof row?.model === "string" ? row.model : null;
      if (!value) continue;
      candidates.push({
        value,
        description: typeof row?.description === "string" ? row.description : null,
        local: downloaded.has(value),
      });
    }

    // A cached model the catalogue did not list is still a real choice: the
    // catalogue needs the network and the cache does not, so an offline run
    // can have the second without the first.
    for (const value of downloaded) {
      if (!candidates.some((c) => c.value === value)) {
        candidates.push({ value, description: null, local: true });
      }
    }

    return candidates.length
      ? { parameter, candidates, hasLocality: true }
      : null;
  }

  if (parameter === "scmayomap_tissue") {
    const tissues = Array.isArray(evidence.available_tissues)
      ? evidence.available_tissues.filter((t): t is string => typeof t === "string")
      : [];
    return tissues.length
      ? {
          parameter,
          // Nothing is downloaded here — the marker database ships with the
          // repository — so locality is not a fact about a tissue and is left
          // null rather than answered `true` for a question nobody asked.
          candidates: tissues.map((value) => ({ value, description: null, local: null })),
          hasLocality: false,
        }
      : null;
  }

  return null;
}

/** Case-insensitive match over both the name and its description. */
export function filterCandidates(candidates: GateCandidate[], query: string): GateCandidate[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return candidates;
  return candidates.filter(
    (c) =>
      c.value.toLowerCase().includes(needle) ||
      (c.description ?? "").toLowerCase().includes(needle),
  );
}

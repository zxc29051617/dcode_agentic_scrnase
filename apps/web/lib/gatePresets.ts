/**
 * Named threshold sets a person can pick instead of filling in three boxes.
 *
 * `gateCandidates.ts` answers "what values may this *one* parameter take". A QC
 * gate asks something else: `apply_cell_qc_filter` offers `min_genes`,
 * `min_counts` and `max_pct_mito` together, and the decision a person is
 * actually making is not three independent numbers but one posture — cut the
 * tail, keep everything, be strict. Three empty inputs make somebody rebuild
 * that posture from a percentile table every time.
 *
 * So this module reads the same evidence the card already receives and returns
 * a handful of complete, named sets. Each one is a `revise` with its overrides
 * pre-filled; the gate protocol underneath is untouched.
 *
 * ## Where a preset comes from is part of the preset
 *
 * `source: "judge"` is the set the judge advised, carrying its own `rationale`
 * and `confidence` verbatim. That text was written during the run's own judge
 * call, is already in the audit log, and is what makes a "recommended" marker
 * something other than the page having an opinion.
 *
 * `source: "evidence"` is derived here, by stepping one row along the preview
 * table the step itself wrote. Nothing is invented: every number in a preset
 * appears in `evidence`, and a criterion with no preview row simply does not
 * appear in the preset.
 *
 * ## What this must never do
 *
 * **Never add two criteria's removal counts.** Each preview row is that
 * criterion applied *alone*, and the cuts overlap:
 * `prompts/steps/apply_cell_qc_filter.md` records 26 + 72 producing 74
 * removals, and the run of 2026-08-22 produced 87 + 105 + 108 = 300 by
 * addition against 167 actually removed, because 83 cells failed more than
 * one. A total presented beside a preset would be wrong by that overlap and
 * would look authoritative. Facts are returned per criterion for that reason,
 * and the caller has nothing to sum.
 */

export type PresetSource = "judge" | "evidence";

/** One criterion inside a preset, with only numbers the evidence recorded. */
export type PresetFact = {
  parameter: string;
  threshold: number;
  /** How many cells this criterion removes **on its own**. Never summed. */
  cellsRemoved: number | null;
  pctRemoved: number | null;
  cellsKept: number | null;
  /** Where this run's own distribution sits, when the step recorded it. */
  median: number | null;
  p90: number | null;
  p95: number | null;
};

export type GatePreset = {
  key: string;
  /** Overrides as strings, because the controller converts, not the browser. */
  overrides: Record<string, string>;
  facts: PresetFact[];
  source: PresetSource;
  /** The judge's own words, verbatim, or null for a derived preset. */
  rationale: string | null;
  confidence: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Which direction is *looser* for this criterion.
 *
 * Read from the name rather than a table: every criterion this step offers is
 * `min_something` or `max_something`, and a table would be a second place to
 * update when a fourth one arrives.
 */
function looserIsHigher(parameter: string): boolean | null {
  if (parameter.startsWith("max_")) return true;
  if (parameter.startsWith("min_")) return false;
  return null;
}

type PreviewRow = {
  threshold: number;
  cellsRemoved: number | null;
  pctRemoved: number | null;
  cellsKept: number | null;
};

function previewRows(evidence: Record<string, unknown>, parameter: string): PreviewRow[] {
  const preview = asRecord(evidence.preview);
  const rows = preview && Array.isArray(preview[parameter]) ? preview[parameter] : [];
  const parsed: PreviewRow[] = [];
  for (const entry of rows as unknown[]) {
    const row = asRecord(entry);
    const threshold = num(row?.threshold);
    if (threshold === null) continue;
    parsed.push({
      threshold,
      cellsRemoved: num(row?.cells_removed),
      pctRemoved: num(row?.pct_removed),
      cellsKept: num(row?.cells_kept),
    });
  }
  return parsed.sort((a, b) => a.threshold - b.threshold);
}

function distributionOf(evidence: Record<string, unknown>, parameter: string) {
  const distributions = asRecord(evidence.distributions);
  const dist = asRecord(distributions?.[parameter]);
  // The step writes `percentiles: { "50": … }`. Nothing here looks for
  // `median` or `p50`: `lib/thresholdEvidence.ts` records that reading the
  // wrong key silently rendered no median at all on every run for months.
  const percentiles = asRecord(dist?.percentiles);
  return {
    median: num(percentiles?.["50"]),
    p90: num(percentiles?.["90"]),
    p95: num(percentiles?.["95"]),
  };
}

function factFor(
  evidence: Record<string, unknown>,
  parameter: string,
  threshold: number,
): PresetFact {
  const rows = previewRows(evidence, parameter);
  const row = rows.find((r) => r.threshold === threshold) ?? null;
  const dist = distributionOf(evidence, parameter);
  return {
    parameter,
    threshold,
    cellsRemoved: row?.cellsRemoved ?? null,
    pctRemoved: row?.pctRemoved ?? null,
    cellsKept: row?.cellsKept ?? null,
    ...dist,
  };
}

/** The advised value for each offered parameter, from the judge's own advice. */
function advisedValues(
  advice: unknown[] | null | undefined,
  offered: string[],
): Map<string, { value: number; rationale: string | null; confidence: string | null }> {
  const found = new Map<string, { value: number; rationale: string | null; confidence: string | null }>();
  for (const entry of advice ?? []) {
    const row = asRecord(entry);
    const parameter = typeof row?.parameter === "string" ? row.parameter : null;
    const value = num(row?.suggested_value);
    if (!parameter || value === null || !offered.includes(parameter)) continue;
    found.set(parameter, {
      value,
      rationale: typeof row?.rationale === "string" ? row.rationale : null,
      confidence: typeof row?.confidence === "string" ? row.confidence : null,
    });
  }
  return found;
}

/**
 * Step `parameter` one preview row away from `from`, in the given direction.
 *
 * Returns null at the end of the table rather than clamping to the row already
 * used: an option identical to the recommended one is not an alternative, and
 * offering it as one would misrepresent the table's extent.
 */
function neighbour(
  evidence: Record<string, unknown>,
  parameter: string,
  from: number,
  looser: boolean,
): number | null {
  const higherIsLooser = looserIsHigher(parameter);
  if (higherIsLooser === null) return null;
  const wantHigher = looser === higherIsLooser;
  const rows = previewRows(evidence, parameter);
  const candidates = wantHigher
    ? rows.filter((r) => r.threshold > from)
    : rows.filter((r) => r.threshold < from).reverse();
  return candidates.length ? candidates[0].threshold : null;
}

/**
 * The named sets this gate can offer, most recommended first.
 *
 * Returns `[]` where nothing can be built — no advice, no preview, or a step
 * that does not work this way — and the caller falls back to the plain inputs,
 * which stay the answer for a value nobody enumerated.
 */
export function presetsFor(gate: {
  step?: string | null;
  revisable?: string[] | null;
  advice?: unknown[] | null;
  evidence?: Record<string, unknown> | null;
}): GatePreset[] {
  const evidence = gate.evidence ?? null;
  if (!evidence) return [];
  const offered = gate.revisable ?? [];
  if (!offered.length) return [];

  const advised = advisedValues(gate.advice, offered);
  if (!advised.size) return [];

  const presets: GatePreset[] = [];

  // 1. What the judge advised, in the judge's own words.
  const judgeOverrides: Record<string, string> = {};
  const judgeFacts: PresetFact[] = [];
  const rationales: string[] = [];
  let confidence: string | null = null;
  for (const parameter of offered) {
    const entry = advised.get(parameter);
    if (!entry) continue;
    judgeOverrides[parameter] = String(entry.value);
    judgeFacts.push(factFor(evidence, parameter, entry.value));
    if (entry.rationale) rationales.push(entry.rationale);
    // The weakest confidence any part of the set carries. A set is only as
    // well-supported as its least-supported member, and reporting the highest
    // would describe a recommendation nobody made.
    if (entry.confidence) {
      const rank = ["low", "medium", "high"];
      confidence =
        confidence === null || rank.indexOf(entry.confidence) < rank.indexOf(confidence)
          ? entry.confidence
          : confidence;
    }
  }
  presets.push({
    key: "advised",
    overrides: judgeOverrides,
    facts: judgeFacts,
    source: "judge",
    rationale: rationales.length ? rationales.join(" ") : null,
    confidence,
  });

  // 2 and 3. One row looser and one row stricter, derived from the same table.
  for (const [key, looser] of [
    ["looser", true],
    ["stricter", false],
  ] as const) {
    const overrides: Record<string, string> = {};
    const facts: PresetFact[] = [];
    for (const parameter of offered) {
      const entry = advised.get(parameter);
      if (!entry) continue;
      const value = neighbour(evidence, parameter, entry.value, looser);
      if (value === null) continue;
      overrides[parameter] = String(value);
      facts.push(factFor(evidence, parameter, value));
    }
    // A set that moved nothing is the advised set under another name.
    if (!facts.length) continue;
    presets.push({
      key,
      overrides,
      facts,
      source: "evidence",
      rationale: null,
      confidence: null,
    });
  }

  return presets;
}

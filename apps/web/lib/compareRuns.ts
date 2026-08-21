import type { RunSummary } from "./gatewayTypes";

export type CompareGroup = {
  input_ref: string;
  runs: RunSummary[];
};

export type CompareSelection = {
  input_ref: string | null;
  left: string | null;
  right: string | null;
};

function startedAtValue(run: RunSummary): number {
  const stamp = run.started_at ? Date.parse(run.started_at) : NaN;
  return Number.isFinite(stamp) ? stamp : 0;
}

export function sortRunsByRecency(runs: RunSummary[]): RunSummary[] {
  return [...runs].sort((a, b) => {
    const delta = startedAtValue(b) - startedAtValue(a);
    return delta !== 0 ? delta : b.scientific_run_id.localeCompare(a.scientific_run_id);
  });
}

export function compareGroups(runs: RunSummary[]): CompareGroup[] {
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    if (!run.input_ref) continue;
    const existing = groups.get(run.input_ref) ?? [];
    existing.push(run);
    groups.set(run.input_ref, existing);
  }
  return [...groups.entries()]
    .map(([input_ref, groupRuns]) => ({ input_ref, runs: sortRunsByRecency(groupRuns) }))
    .filter((group) => group.runs.length >= 2)
    .sort((a, b) => {
      const delta = startedAtValue(b.runs[0]) - startedAtValue(a.runs[0]);
      return delta !== 0 ? delta : a.input_ref.localeCompare(b.input_ref);
    });
}

export function defaultCompareSelection(
  groups: CompareGroup[],
  requestedInputRef?: string | null,
  requestedLeft?: string | null,
  requestedRight?: string | null,
): CompareSelection {
  const group =
    groups.find((candidate) => candidate.input_ref === requestedInputRef) ?? groups[0] ?? null;
  if (!group) return { input_ref: null, left: null, right: null };

  const fallbackLeft = group.runs[0]?.scientific_run_id ?? null;
  const fallbackRight = group.runs.find((run) => run.scientific_run_id !== fallbackLeft)?.scientific_run_id ?? null;
  const left =
    group.runs.find((run) => run.scientific_run_id === requestedLeft)?.scientific_run_id ?? fallbackLeft;
  const rightCandidate =
    group.runs.find(
      (run) =>
        run.scientific_run_id === requestedRight && run.scientific_run_id !== left,
    )?.scientific_run_id ?? fallbackRight;
  const right = rightCandidate !== left ? rightCandidate : fallbackRight !== left ? fallbackRight : null;

  return {
    input_ref: group.input_ref,
    left,
    right,
  };
}

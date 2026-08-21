/**
 * Reading what `apply_cell_qc_filter` recorded before it filtered anything.
 *
 * The step writes two blocks into its gate evidence, and they answer different
 * halves of the same question:
 *
 *     distributions[criterion] = { percentiles: { "1": …, "50": …, "99": … },
 *                                  min, max }
 *     preview[criterion]       = [ { threshold, cells_removed, cells_kept,
 *                                    pct_removed }, … ]
 *
 * `preview` is what each candidate cut would cost. `distributions` is where
 * this run's data actually sits, and the median is the single number that turns
 * the first table from a list of numbers into a decision — a `max_pct_mito` of
 * 15 means something quite different on a run whose median is 5.4 than on one
 * whose median is 14.
 *
 * ## The bug this file exists to fix
 *
 * The table looked for `median`, `p50` and `50%`. The step has never written
 * any of the three; it writes `percentiles["50"]`. So "this run's median" was
 * never rendered on any run, on any criterion, since the table was added —
 * silently, because a missing median is indistinguishable from a distribution
 * nobody recorded.
 *
 * ## Why absent is null and never zero
 *
 * `max_pct_erythroid` on a clean PBMC preparation has a median of exactly 0.0,
 * which is a real measurement and reads correctly on the page. A `?? 0` or a
 * falsy check anywhere in here would make that number and a distribution the
 * run never recorded render identically — the failure `docs/report_contract.md`
 * calls reconstructing a gap rather than stating it.
 */

/** One row of a criterion's cost table, as the step writes it. */
export type ThresholdRow = {
  threshold: number | string;
  cells_removed?: number;
  cells_kept?: number;
  pct_removed?: number;
};

function isRow(value: unknown): value is ThresholdRow {
  return Boolean(value) && typeof value === "object" && "threshold" in (value as object);
}

function isRows(value: unknown): value is ThresholdRow[] {
  return Array.isArray(value) && value.length > 0 && value.every(isRow);
}

/**
 * The criteria in `preview` that actually carry rows, in recorded order.
 *
 * Everything else is skipped rather than rejected. Evidence is written by a
 * skill and projected through two services; a renamed or reshaped field has to
 * degrade to "no table" inside the one control a person needs in order to
 * answer a paused run, not throw inside it.
 */
export function criterionRows(
  preview: Record<string, unknown> | null | undefined,
): [string, ThresholdRow[]][] {
  if (!preview || typeof preview !== "object") return [];
  return Object.entries(preview).filter(([, rows]) => isRows(rows)) as [string, ThresholdRow[]][];
}

//: Where a median may be found, in the order tried. The first is what this
//: pipeline writes; the rest are shapes a different producer plausibly uses,
//: kept so that reading one recorded number does not depend on one spelling.
const MEDIAN_KEYS = ["median", "p50", "50%", "50"] as const;

function numberAt(record: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

/**
 * The median out of whatever shape a distribution was recorded in, or null.
 *
 * Null means nobody recorded one, and the caller must render it as an absence
 * rather than as a number — see the note above about 0.0 being a real value.
 */
export function medianOf(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;

  const record = value as Record<string, unknown>;
  const direct = numberAt(record, MEDIAN_KEYS);
  if (direct !== null) return direct;

  // What this pipeline actually writes. Numeric keys are read as well as
  // string ones: JSON gives strings, a hand-assembled fixture may not.
  const percentiles = record.percentiles;
  if (percentiles && typeof percentiles === "object" && !Array.isArray(percentiles)) {
    return numberAt(percentiles as Record<string, unknown>, MEDIAN_KEYS);
  }
  return null;
}

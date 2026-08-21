/**
 * What each candidate cut would cost, as a table somebody can read.
 *
 * `apply_cell_qc_filter` records this before it filters anything: for every
 * threshold on every criterion, how many cells it would remove and how many
 * would survive. It is the entire answer to the question the gate asks, and
 * it was on the page already — inside a `<details>` labelled "Full recorded
 * evidence (JSON, for checking)", as a two-hundred-line blob.
 *
 * That is the same failure this project keeps finding: the evidence reaches
 * disk, reaches the browser, and stops one step short of the person. A reader
 * who cannot parse JSON in their head had the reviewer's one-line summary and
 * nothing else, and the summary says what was measured rather than what each
 * choice costs.
 *
 * The JSON stays, for checking what the executor actually wrote. This is what
 * the decision is made from.
 *
 * ## Nothing here recommends anything
 *
 * No row is highlighted, no threshold is called sensible, and the medians are
 * shown beside the table rather than folded into a suggestion. A default drawn
 * on this table would be a decision made by the page, and the whole reason
 * this step stops is that filtering is destructive and the choice is the
 * operator's.
 */

import { criterionRows, medianOf } from "@/lib/thresholdEvidence";

const CRITERION_TITLES: Record<string, { title: string; what: string }> = {
  min_genes: {
    title: "Fewest genes a cell may have",
    what: "A droplet with very few genes is usually debris or an empty bead rather than a cell.",
  },
  min_counts: {
    title: "Fewest UMI counts a cell may have",
    what: "The same judgement by total molecules rather than by distinct genes.",
  },
  max_pct_mito: {
    title: "Most mitochondrial signal a cell may have",
    what:
      "A dying cell leaks cytoplasmic RNA while its mitochondria stay, so the mitochondrial " +
      "fraction rises. This is the criterion that decides how many dying cells reach the result.",
  },
  max_pct_erythroid: {
    title: "Most haemoglobin signal a cell may have",
    what: "Red blood cell contamination. Usually near zero in a clean PBMC preparation.",
  },
};

export default function ThresholdPreview({
  preview,
  distributions,
  nCells,
}: {
  preview: Record<string, unknown> | undefined;
  distributions?: Record<string, unknown>;
  nCells?: number;
}) {
  const groups = criterionRows(preview);
  if (groups.length === 0) return null;

  return (
    <div data-testid="threshold-preview" style={{ marginTop: "0.8rem" }}>
      <h3 style={{ marginBottom: "0.2rem" }}>What each cut would cost</h3>
      <p className="subtle" style={{ marginTop: 0 }}>
        Measured on this run&rsquo;s {nCells != null ? nCells.toLocaleString() : ""} cells before
        anything was removed. Nothing has been filtered yet — choosing <strong>Revise</strong> and
        entering a value is what applies one.
      </p>

      {groups.map(([criterion, rows]) => {
        const meta = CRITERION_TITLES[criterion];
        const median = medianOf(distributions?.[criterion]);
        return (
          <div key={criterion} style={{ marginTop: "0.9rem" }}>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline", flexWrap: "wrap" }}>
              <strong>{meta?.title ?? criterion}</strong>
              <code className="tl-id">{criterion}</code>
              {median != null && (
                <span className="subtle">this run&rsquo;s median: {formatNumber(median)}</span>
              )}
            </div>
            {meta?.what && (
              <p className="subtle" style={{ margin: "0.15rem 0 0.35rem" }}>
                {meta.what}
              </p>
            )}
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Threshold</th>
                    <th className="num">Cells removed</th>
                    <th className="num">Of the run</th>
                    <th className="num">Cells kept</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={String(row.threshold)}>
                      <td>
                        <code>{String(row.threshold)}</code>
                      </td>
                      <td className="num">{formatNumber(row.cells_removed)}</td>
                      <td className="num">
                        {row.pct_removed != null ? `${row.pct_removed}%` : "—"}
                      </td>
                      <td className="num">{formatNumber(row.cells_kept)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function formatNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US");
}

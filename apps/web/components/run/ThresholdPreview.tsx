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
    title: "一顆細胞最少要偵測到幾個基因",
    what: "基因數很少的液滴通常是碎屑或空的 bead，不是細胞。",
  },
  min_counts: {
    title: "一顆細胞最少要有幾個 UMI",
    what: "同一個判斷，但看的是分子總數而不是相異基因數。",
  },
  max_pct_mito: {
    title: "一顆細胞最多容許多少粒線體訊號",
    what:
      "將死的細胞會漏出細胞質 RNA，粒線體卻留著，所以粒線體比例會升高。" +
      "這一條決定了有多少將死的細胞會進到最後的結果裡。",
  },
  max_pct_erythroid: {
    title: "一顆細胞最多容許多少血紅蛋白訊號",
    what: "紅血球污染。乾淨的 PBMC 製備通常接近零。",
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
      <h3 style={{ marginBottom: "0.2rem" }}>每一刀會付出什麼代價</h3>
      <p className="subtle" style={{ marginTop: 0 }}>
        在這次執行的 {nCells != null ? nCells.toLocaleString() : ""} 顆細胞上量的，
        量的時候還沒移除任何東西。目前什麼都還沒過濾 —— 要選<strong>改參數重跑</strong>
        並填入數值，才會真的套用。
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
                <span className="subtle">本次中位數：{formatNumber(median)}</span>
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
                    <th>閾值</th>
                    <th className="num">移除細胞數</th>
                    <th className="num">佔比</th>
                    <th className="num">保留細胞數</th>
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

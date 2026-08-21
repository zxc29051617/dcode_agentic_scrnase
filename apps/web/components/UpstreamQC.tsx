import type { UpstreamDetail } from "@/lib/gatewayTypes";

/**
 * FastQC and Cell Ranger's own numbers, rendered natively.
 *
 * These tools each publish an HTML report of their own. The pipeline already
 * parses the numbers out of them — `fastq_qc` keeps per-file FastQC results
 * and `cellranger_count` keeps `metrics_summary.csv` — so the figures a
 * person actually reads can be shown here, in this site's own styling, with
 * no iframe and no artifact endpoint.
 *
 * The full HTML reports are a separate matter: they live inside the run
 * directory and the gateway serves them through `/api/artifacts/...`, so the
 * page can link to the report or sandbox it when one exists. Whether one
 * exists is reported; where it is, deliberately, is not.
 */

/** Cell Ranger metrics worth putting first. The rest follow in recorded order. */
const CELLRANGER_HEADLINE = [
  "Estimated Number of Cells",
  "Mean Reads per Cell",
  "Median Genes per Cell",
  "Sequencing Saturation",
  "Q30 Bases in RNA Read",
  "Reads Mapped Confidently to Transcriptome",
];

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

export default function UpstreamQC({ detail }: { detail: UpstreamDetail }) {
  // `libraries` present means this is the Cell Ranger projection; the two
  // shapes never overlap, because the gateway builds one projection per step.
  if (detail.libraries) return <Cellranger libraries={detail.libraries} />;
  return <FastqQC detail={detail} />;
}

function Cellranger({ libraries }: { libraries: NonNullable<UpstreamDetail["libraries"]> }) {
  if (libraries.length === 0) {
    return <p className="subtle">No library was recorded for this step.</p>;
  }
  return (
    <>
      {libraries.map((library) => {
        const metrics = library.metrics_summary ?? {};
        const keys = Object.keys(metrics);
        const headline = CELLRANGER_HEADLINE.filter((k) => k in metrics);
        const rest = keys.filter((k) => !headline.includes(k));
        return (
          <div key={library.library_id ?? "library"} style={{ marginTop: "0.7rem" }}>
            <p className="subtle" style={{ margin: "0 0 0.35rem" }}>
              Cell Ranger · <code>{library.library_id}</code>
              {library.chemistry ? ` · ${library.chemistry}` : ""}
              {library.has_web_summary && " · a web summary was produced"}
            </p>

            {keys.length === 0 ? (
              <p className="subtle" style={{ margin: 0 }}>
                No <code>metrics_summary.csv</code> was recorded for this library.
              </p>
            ) : (
              <>
                <div className="cards" style={{ margin: "0 0 0.6rem" }}>
                  {headline.map((key) => (
                    <div className="card" key={key}>
                      <div className="card-label">{key}</div>
                      <div className="card-value" style={{ fontSize: "1.25rem" }}>
                        {metrics[key]}
                      </div>
                    </div>
                  ))}
                </div>
                {rest.length > 0 && (
                  <dl className="kv">
                    {rest.map((key) => (
                      <div key={key} style={{ display: "contents" }}>
                        <dt>{key}</dt>
                        <dd className="num">{metrics[key]}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </>
            )}
          </div>
        );
      })}
    </>
  );
}

function FastqQC({ detail }: { detail: UpstreamDetail }) {
  const roles = Object.entries(detail.per_read_role ?? {});
  const files = detail.files ?? [];
  const failures = Object.entries(detail.module_failures ?? {});

  return (
    <div style={{ marginTop: "0.7rem" }}>
      <p className="subtle" style={{ margin: "0 0 0.35rem" }}>
        FastQC{detail.has_multiqc_report && " · a MultiQC report was produced"}
      </p>

      {roles.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Read</th>
                <th className="num">Files</th>
                <th className="num">Reads</th>
                <th className="num">Q30</th>
                <th className="num">Duplicate</th>
              </tr>
            </thead>
            <tbody>
              {roles.map(([role, stats]) => (
                <tr key={role}>
                  <td>
                    <code>{role}</code>
                    {role === "R2" && <span className="subtle"> cDNA</span>}
                    {role === "R1" && <span className="subtle"> barcode+UMI</span>}
                  </td>
                  <td className="num">{stats.n_files}</td>
                  <td className="num">{count(stats.total_sequences)}</td>
                  <td className="num">{pct(stats.q30_fraction)}</td>
                  <td className="num">{pct(stats.duplicate_fraction)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {failures.length > 0 && (
        <>
          <p className="subtle" style={{ margin: "0.7rem 0 0.2rem" }}>
            FastQC module failures
          </p>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {failures.map(([file, modules]) => (
              <li key={file}>
                <code>{file}</code> — {modules.join(", ")}
              </li>
            ))}
          </ul>
        </>
      )}

      {(detail.expected_module_flags?.length ?? 0) > 0 && (
        <p className="subtle" style={{ margin: "0.5rem 0 0" }}>
          Expected for 10x and not counted as findings: {" "}
          {detail.expected_module_flags!.join(", ")}
        </p>
      )}

      {files.length > 0 && (
        <>
          <p className="subtle" style={{ margin: "0.7rem 0 0.2rem" }}>
            Per file
            {detail.files_total !== undefined &&
              detail.files_shown !== undefined &&
              detail.files_shown < detail.files_total &&
              ` — showing ${detail.files_shown} of ${detail.files_total}`}
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Read</th>
                  <th className="num">Length</th>
                  <th className="num">Reads</th>
                  <th className="num">Q30</th>
                  <th className="num">Adapter max</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr key={file.file}>
                    <td style={{ fontFamily: "ui-monospace, Menlo, monospace" }}>{file.file}</td>
                    <td>{file.read_role ?? "—"}</td>
                    <td className="num">{file.sequence_length ?? "—"}</td>
                    <td className="num">{count(file.total_sequences)}</td>
                    <td className="num">{pct(file.q30_fraction)}</td>
                    <td className="num">
                      {file.max_adapter_pct === null || file.max_adapter_pct === undefined
                        ? "—"
                        : `${file.max_adapter_pct.toFixed(1)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {(detail.notes?.length ?? 0) > 0 && (
        <>
          <p className="subtle" style={{ margin: "0.7rem 0 0.2rem" }}>
            Notes
          </p>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {detail.notes!.map((note, i) => (
              <li key={i} className="subtle">
                {note}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

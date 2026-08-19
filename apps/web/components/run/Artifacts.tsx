import type { ArtifactEntry } from "@/lib/gatewayTypes";

/** Human-readable size, so a 40 MB MultiQC report is obviously a 40 MB one. */
function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

const KIND_ORDER = [
  "multiqc_html",
  "fastqc_html",
  "cellranger_web_summary",
  "embedding_html",
  "embedding_json",
  "report_html",
  "report_pdf",
  "figure",
];

/**
 * Every file this run produced that the gateway will serve.
 *
 * Collapsed by default and last but one in the document. It is a file manifest,
 * not a finding: somebody reading a run does not want it, and somebody
 * downloading its figures wants nothing else. The count is in the summary so
 * it is never a mystery whether opening it is worth it.
 */
export default function Artifacts({ runId, artifacts }: { runId: string; artifacts: ArtifactEntry[] }) {
  const entries = [...artifacts].sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || a.name.localeCompare(b.name),
  );

  if (entries.length === 0) {
    return (
      <p className="subtle">
        This run published no servable artifact. A run that stopped before <code>build_report</code>{" "}
        has no figures, and one taken from a count matrix has no FastQC or Cell Ranger output.
      </p>
    );
  }

  return (
    <>
      <p className="subtle">
        The list is the whole of it: a file that does not appear here cannot be fetched, because a
        request names an id this manifest produced and never a path.
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>File</th>
              <th>Kind</th>
              <th>Where in the run</th>
              <th className="num">Size</th>
              <th>Open</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => {
              const href = `/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(entry.artifact_id)}`;
              return (
                <tr key={entry.artifact_id}>
                  <td>{entry.name}</td>
                  <td>
                    <span className="badge" data-tone="muted">
                      {entry.label}
                    </span>
                  </td>
                  <td>
                    <code className="subtle">{entry.relative_path}</code>
                  </td>
                  <td className="num">{size(entry.size_bytes)}</td>
                  <td>
                    {entry.too_large ? (
                      <span className="subtle">too large to serve</span>
                    ) : (
                      <>
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          open ↗
                        </a>
                        {" · "}
                        <a href={`${href}?download=1`}>download</a>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="subtle" style={{ marginTop: "1rem" }}>
        Opening a report loads it as its own sandboxed document. It runs with an opaque origin and
        cannot read anything belonging to this site.
      </p>
    </>
  );
}

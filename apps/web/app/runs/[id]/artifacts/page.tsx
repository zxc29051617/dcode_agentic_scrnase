import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import { getRunSnapshot, listArtifacts } from "@/lib/gateway";

export const dynamic = "force-dynamic";

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
  "report_html",
  "report_pdf",
  "figure",
];

export default async function ArtifactsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, artifacts] = await Promise.all([getRunSnapshot(id), listArtifacts(id)]);
  if (!snapshot) notFound();

  const entries = [...(artifacts ?? [])].sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || a.name.localeCompare(b.name),
  );

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Artifacts</h1>
      <p className="subtle">
        Every file in this run the gateway will serve. The list is the whole of it: a file that
        does not appear here cannot be fetched, because a request names an id this manifest
        produced and never a path.
      </p>

      {entries.length === 0 ? (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Not recorded</h2>
          <p style={{ margin: 0 }}>
            This run published no servable artifact. A run that halted before <code>build_report</code>{" "}
            has no figures, and one taken from a count matrix has no FastQC or Cell Ranger output.
          </p>
        </div>
      ) : (
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
                const href = `/api/artifacts/${encodeURIComponent(id)}/${encodeURIComponent(entry.artifact_id)}`;
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
      )}

      <p className="subtle" style={{ marginTop: "1rem" }}>
        Opening a report loads it as its own sandboxed document. It runs with an opaque origin and
        cannot read anything belonging to this site.
      </p>
    </RunShell>
  );
}

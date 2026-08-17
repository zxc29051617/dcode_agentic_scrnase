import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import ReportReader from "@/components/ReportReader";
import ArtifactFrame from "@/components/ArtifactFrame";
import EmbeddingViewer from "@/components/EmbeddingViewer";
import { getReport, getRunSnapshot, listArtifacts } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, report, artifacts] = await Promise.all([
    getRunSnapshot(id),
    getReport(id),
    listArtifacts(id),
  ]);
  if (!snapshot || !report) notFound();

  const entries = artifacts ?? [];
  const figures = Object.fromEntries(
    entries.filter((e) => e.kind === "figure").map((e) => [e.name, e.artifact_id]),
  );
  const embeddingArtifacts = Object.fromEntries(
    entries.filter((e) => e.kind === "embedding_html").map((e) => [e.name, e.artifact_id]),
  );
  const reportHtml = entries.find((e) => e.kind === "report_html");
  const embeddings = entries
    .filter((e) => e.kind === "embedding_html")
    .sort((a, b) => a.name.localeCompare(b.name));
  const embeddingDataArtifacts = entries
    .filter((e) => e.kind === "embedding_json")
    .sort((a, b) => a.name.localeCompare(b.name));

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Report</h1>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Interactive embedding viewer</h2>
        <p className="subtle" style={{ marginTop: 0 }}>
          Choose the embedding view and metadata coloring below. Hover cells, zoom, and rotate
          3D views directly in the application.
        </p>
        {embeddingDataArtifacts.length > 0 ? (
          <EmbeddingViewer
            runId={id}
            dataArtifacts={embeddingDataArtifacts}
            standaloneArtifacts={embeddings}
          />
        ) : embeddings.length === 0 ? (
          <p style={{ margin: 0 }}>
            No Plotly embedding data was recorded for this run. Run <code>run_umap</code>
            with the Plotly-enabled <code>build_report</code> to publish 2D or 3D views here.
          </p>
        ) : (
          <p style={{ margin: 0 }}>
            Standalone Plotly files were recorded, but interactive viewer data was not published.
          </p>
        )}
      </div>

      {reportHtml && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Standalone report HTML</h2>
          <p className="subtle" style={{ marginTop: 0 }}>
            Optional pipeline-rendered HTML for archival or download. The interactive viewer above
            is the primary way to inspect embeddings in this application.
          </p>
          <ArtifactFrame runId={id} artifact={reportHtml} height="60vh" />
        </div>
      )}

      {report.available && report.content ? (
        <ReportReader
          content={report.content}
          sourcePath={report.source_path}
          runId={id}
          figures={figures}
          embeddings={embeddingArtifacts}
        />
      ) : (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>No report</h2>
          <p style={{ margin: 0 }}>{report.reason}</p>
        </div>
      )}
    </RunShell>
  );
}

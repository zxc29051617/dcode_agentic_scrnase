import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import ReportReader from "@/components/ReportReader";
import ArtifactFrame from "@/components/ArtifactFrame";
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
  // Figure name -> artifact id. Built from the manifest, so a markdown
  // `![](figures/anything.png)` can only ever resolve to a figure this run
  // genuinely published.
  const figures = Object.fromEntries(
    entries.filter((e) => e.kind === "figure").map((e) => [e.name, e.artifact_id]),
  );
  const reportHtml = entries.find((e) => e.kind === "report_html");

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Report</h1>

      {reportHtml && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>The report as build_report rendered it</h2>
          <p className="subtle" style={{ marginTop: 0 }}>
            Same content as below, in the pipeline&apos;s own single-file HTML with its figures
            inlined. Shown in an isolated frame.
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

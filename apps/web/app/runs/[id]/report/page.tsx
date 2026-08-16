import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import ReportReader from "@/components/ReportReader";
import { getReport, getRunSnapshot } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, report] = await Promise.all([getRunSnapshot(id), getReport(id)]);
  if (!snapshot || !report) notFound();

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Report</h1>
      {report.available && report.content ? (
        <ReportReader content={report.content} sourcePath={report.source_path} />
      ) : (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>No report</h2>
          <p style={{ margin: 0 }}>{report.reason}</p>
        </div>
      )}
    </RunShell>
  );
}

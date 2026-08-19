import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import WorkflowTimeline from "@/components/WorkflowTimeline";
import { getRunSnapshot, getStepRecords } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function WorkflowPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, steps] = await Promise.all([getRunSnapshot(id), getStepRecords(id)]);
  if (!snapshot || !steps) notFound();

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Workflow</h1>
      <p className="subtle">
        Steps in the order the audit log recorded them. Select one to see the judge&apos;s reasons,
        its warnings and the metrics it recorded.
      </p>

      <div className="controls" style={{ gap: "0.75rem" }}>
        <Legend tone="pass" label="pass" />
        <Legend tone="warn" label="warn" />
        <Legend tone="fail" label="fail" />
        <Legend tone="reused" label="reused" />
        <Legend tone="muted" label="no verdict" />
      </div>

      <div className="panel">
        <WorkflowTimeline steps={steps} runId={id} />
      </div>
    </RunShell>
  );
}

function Legend({ tone, label }: { tone: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}>
      <span className="tl-dot" data-tone={tone} />
      <span className="subtle">{label}</span>
    </span>
  );
}

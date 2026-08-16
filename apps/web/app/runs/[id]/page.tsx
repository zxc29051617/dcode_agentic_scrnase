import Link from "next/link";
import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import Badge from "@/components/Badge";
import { getRunSnapshot } from "@/lib/gateway";
import { formatTime, stepTone } from "@/lib/verdict";

export const dynamic = "force-dynamic";

export default async function RunOverviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const snapshot = await getRunSnapshot(id);
  if (!snapshot) notFound();

  const done = snapshot.steps.filter((s) => s.status !== "unknown").length;
  const base = `/runs/${encodeURIComponent(id)}`;

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Run overview</h1>
      <p className="subtle">
        started {formatTime(snapshot.started_at)}
        {snapshot.species ? ` · species ${snapshot.species}` : " · species not recorded"}
      </p>

      <SummaryCards
        items={[
          { label: "Cells", value: snapshot.cells, title: "from annotate_cells or apply_cell_qc_filter" },
          { label: "Clusters", value: snapshot.clusters, title: "from run_clustering or cross_check_annotation" },
          { label: "Cell types", value: snapshot.cell_types, title: "from annotate_cells" },
          { label: "Steps recorded", value: snapshot.steps.length },
          { label: "Judge warnings", value: snapshot.warn_count },
          { label: "Judge failures", value: snapshot.fail_count },
        ]}
      />

      {snapshot.pending_gate && (
        <div className="panel" data-tone="warn">
          <h2 style={{ marginTop: 0 }}>Waiting for human review</h2>
          <p style={{ marginTop: 0 }}>
            <code>{snapshot.pending_gate.step}</code> ({snapshot.pending_gate.gate}) — verdict{" "}
            <strong>{snapshot.pending_gate.verdict}</strong>
            {snapshot.pending_gate.score !== null && ` · score ${snapshot.pending_gate.score}`}
          </p>
          <ul style={{ marginTop: 0 }}>
            {snapshot.pending_gate.reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
          {snapshot.pending_gate.revisable.length > 0 && (
            <p className="subtle">
              revisable at this gate: {snapshot.pending_gate.revisable.join(", ")}
            </p>
          )}
          <p className="subtle" style={{ marginBottom: 0 }}>
            This page has no accept / revise / stop control. The decision is answered at the
            terminal, and the operator identity is recorded there.
          </p>
        </div>
      )}

      <h2>Workflow progress</h2>
      <div className="panel">
        <p className="subtle" style={{ marginTop: 0 }}>
          {done} of {snapshot.steps.length} recorded steps have an outcome
          {snapshot.reused_steps.length > 0 && ` · ${snapshot.reused_steps.length} reused`}
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
          {snapshot.steps.map((step) => (
            <span
              key={step.step}
              className="tl-dot"
              data-tone={stepTone(step.status, step.verdict)}
              title={`${step.step} — ${step.status}${step.verdict ? ` (${step.verdict})` : ""}`}
              style={{ width: "1.4rem", height: "0.45rem", borderRadius: "3px" }}
            />
          ))}
        </div>
        <p style={{ marginBottom: 0, marginTop: "0.8rem" }}>
          <Link href={`${base}/workflow`}>Open the workflow timeline →</Link>
        </p>
      </div>

      {snapshot.reused_steps.length > 0 && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Reused work</h2>
          <p className="subtle" style={{ marginTop: 0 }}>
            These steps were not re-executed; their results came from an earlier run in the same
            directory.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
            {snapshot.reused_steps.map((step) => (
              <Badge key={step} tone="reused">
                {step}
              </Badge>
            ))}
          </div>
        </div>
      )}

      <h2>Go to</h2>
      <p>
        {snapshot.has_report ? (
          <Link href={`${base}/report`}>Report</Link>
        ) : (
          <span className="subtle">Report — not produced for this run</span>
        )}
        {" · "}
        <Link href={`${base}/provenance`}>Provenance</Link>
        {" · "}
        <Link href={`${base}/workflow`}>Workflow</Link>
      </p>
    </RunShell>
  );
}

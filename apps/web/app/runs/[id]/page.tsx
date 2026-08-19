import Link from "next/link";
import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import Badge from "@/components/Badge";
import GateDecisionCard from "@/components/GateDecisionCard";
import { getRunSnapshot } from "@/lib/gateway";
import { controllerConfigured, getGateState } from "@/lib/controller";
import type { GateState } from "@/lib/controllerTypes";
import { formatTime, stepTone } from "@/lib/verdict";
import { describeAssistantModel } from "@/lib/assistantModel";
import { GATE_ADVISOR_INSTRUCTIONS } from "@/lib/gateAdvisorActions";

export const dynamic = "force-dynamic";

/**
 * The gate this run is waiting at, as the *controller* reads it, or null.
 *
 * Deliberately a second source from the gateway's `pending_gate`. The gateway
 * projects what happened; answering needs `gate_id` and `generation`, which
 * only exist to make a decision refer to one specific pending question, and
 * which the controller derives from the same audit log. Asking the service
 * that will validate the answer what the question is means the page cannot
 * offer a control for a gate the controller would then refuse.
 *
 * A controller that is down or unconfigured returns null and the page falls
 * back to what it always said: answer it at the terminal. That is a real
 * fallback, not a degraded one — the CLI path is unchanged and still works.
 */
async function answerableGate(runId: string): Promise<GateState | null> {
  if (!controllerConfigured()) return null;
  try {
    const state = await getGateState(runId);
    return state.pending_gate && state.gate_id ? state : null;
  } catch {
    return null;
  }
}

export default async function RunOverviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const snapshot = await getRunSnapshot(id);
  if (!snapshot) notFound();

  const gate = await answerableGate(id);
  // Read here rather than inside the card: the card is a Client Component and
  // must never see an environment value, only the boolean derived from one.
  const assistantModel = describeAssistantModel();
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

      {/* A run whose process disappeared mid-step. Not a scientific failure —
          every step before this one is on disk and still valid — so the page
          names the step and the command that picks up from it, rather than
          leaving somebody to work out why a run has been "running" since
          yesterday. */}
      {snapshot.status === "interrupted" && (
        <div className="panel" data-tone="warn" data-testid="interrupted-panel">
          <h2 style={{ marginTop: 0 }}>This run stopped without finishing</h2>
          <p style={{ marginTop: 0 }}>
            It was inside{" "}
            <code>{snapshot.unfinished_step ?? "a step"}</code> when it last wrote anything
            {snapshot.last_activity_at && `, at ${formatTime(snapshot.last_activity_at)}`}. The
            process is gone; the steps that finished before it are intact.
          </p>
          <p className="subtle" style={{ marginBottom: 0 }}>
            Pick it up from the first step it can no longer trust:
            <br />
            <code>python -m src.run --resume-from {id} --input &lt;same input&gt;</code>
          </p>
        </div>
      )}

      {/* Two renderings of one fact, and which one appears depends on whether
          this deployment has a controller. With one, the decision can be made
          here and is validated and attributed server-side. Without one, the
          page says what it always said — the terminal is where this is
          answered — because that path is unchanged and still works. */}
      {gate ? (
        <GateDecisionCard
          state={gate}
          advisorInstructions={GATE_ADVISOR_INSTRUCTIONS}
          modelConfigured={assistantModel.configured}
          modelReason={assistantModel.configured ? null : assistantModel.reason}
        />
      ) : (
        snapshot.pending_gate && (
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
              This deployment has no analysis controller, so there is no accept / revise / stop
              control here. Answer it at the terminal, where the operator identity is recorded:
              <br />
              <code>python -m src.run --continue-from {id} --interactive</code>
            </p>
          </div>
        )
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

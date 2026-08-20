import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import Badge from "@/components/Badge";
import GateDecisionCard from "@/components/GateDecisionCard";
import WorkflowTimeline from "@/components/WorkflowTimeline";
import ReportReader from "@/components/ReportReader";
import EmbeddingViewer from "@/components/EmbeddingViewer";
import Contents from "@/components/run/Contents";
import Quality from "@/components/run/Quality";
import Provenance from "@/components/run/Provenance";
import Artifacts from "@/components/run/Artifacts";
import Progress from "@/components/run/Progress";
import {
  getProvenance,
  getReport,
  getRunSnapshot,
  getStepRecords,
  getStepTimings,
  listArtifacts,
} from "@/lib/gateway";
import { controllerConfigured, getGateState, getLatestJob } from "@/lib/controller";
import type { GateState } from "@/lib/controllerTypes";
import { formatTime } from "@/lib/verdict";
import { describeAssistantModel } from "@/lib/assistantModel";
import { GATE_ADVISOR_INSTRUCTIONS } from "@/lib/gateAdvisorActions";
import { statusWord, stepLabel, STATUS_WORDS } from "@/lib/stepLabels";

export const dynamic = "force-dynamic";

/**
 * One run, as one document.
 *
 * This was six tabs — Overview, Workflow, QC, Report, Artifacts, Provenance —
 * and the tabs were the problem. An analysis is a linear argument: this is what
 * came in, this is what was done to it, this is what it says. Splitting that
 * across six pages asks the reader to reassemble it, and to know in advance
 * which tab holds the part they want. Somebody who already knows the pipeline
 * can do that. The person this tool is for cannot, and the tabs gave them no
 * clue: "QC" and "Report" are equally plausible homes for "is this any good".
 *
 * So the sections are stacked in the order the argument runs, with a contents
 * rail that shows the whole shape at once, and the old routes redirect to the
 * anchors so every existing link and test still lands in the right place.
 *
 * ## What decides the top of the page
 *
 * Not the section order — the run's state. A run waiting at a gate is holding
 * a checkpoint and doing nothing until a person answers, and it will wait
 * indefinitely; that is the only thing worth reading first. A run that stopped
 * unexpectedly needs its resume command, not its findings. Everything else
 * leads with what it found.
 *
 * ## One fetch
 *
 * The six pages each fetched the snapshot, so opening a run and clicking
 * through cost six snapshot reads. This fetches everything once, in parallel,
 * and hands it down. `getReport` and `getProvenance` are allowed to fail: a run
 * that stopped early has neither, and that must render as a stated absence
 * rather than a 404 for the whole document.
 */

/**
 * The gate this run is waiting at, as the *controller* reads it, or null.
 *
 * Deliberately a second source from the gateway's `pending_gate`. The gateway
 * projects what happened; answering needs `gate_id` and `generation`, which
 * only exist to make a decision refer to one specific pending question, and
 * which the controller derives from the same audit log. Asking the service
 * that will validate the answer what the question is means the page cannot
 * offer a control for a gate the controller would then refuse.
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

/**
 * The reason a run stopped, when there was one and nothing else on the page
 * would say so.
 *
 * A closed gate and no report is ambiguous from the audit log: it could be
 * genuinely still working, or the executor could have refused to proceed and
 * recorded exactly why. `apply_cell_qc_filter` demonstrated it — accepted
 * with no thresholds, the step stayed `needs_review`, and the run halted two
 * seconds later. The reason lives in the controller's job record, not on the
 * run directory, so it is fetched from there rather than inferred.
 */
async function haltReason(runId: string, hasGate: boolean, hasReport: boolean): Promise<string | null> {
  if (!controllerConfigured() || hasGate || hasReport) return null;
  const job = await getLatestJob(runId);
  return job?.error ?? null;
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, steps, artifacts, report, provenance, timings] = await Promise.all([
    getRunSnapshot(id),
    getStepRecords(id).catch(() => null),
    listArtifacts(id).catch(() => null),
    getReport(id).catch(() => null),
    getProvenance(id).catch(() => null),
    // Measured on this machine, from its own finished runs. Never a hardcoded
    // expectation: `cellranger_count` on a 1k library and a 10k one differ by
    // more than any written number survives, and being wrong here sends
    // somebody to investigate a run that is fine.
    getStepTimings().catch(() => null),
  ]);
  if (!snapshot) notFound();

  const gate = await answerableGate(id);
  const halted = await haltReason(id, Boolean(gate ?? snapshot.pending_gate), snapshot.has_report);
  // Read here rather than inside the card: the card is a Client Component and
  // must never see an environment value, only the boolean derived from one.
  const assistantModel = describeAssistantModel();

  const entries = artifacts ?? [];
  const figures = Object.fromEntries(
    entries.filter((e) => e.kind === "figure").map((e) => [e.name, e.artifact_id]),
  );
  const embeddingArtifacts = Object.fromEntries(
    entries.filter((e) => e.kind === "embedding_html").map((e) => [e.name, e.artifact_id]),
  );
  const embeddings = entries
    .filter((e) => e.kind === "embedding_html")
    .sort((a, b) => a.name.localeCompare(b.name));
  const embeddingData = entries
    .filter((e) => e.kind === "embedding_json")
    .sort((a, b) => a.name.localeCompare(b.name));

  const hasReport = Boolean(report?.available && report.content);
  const done = snapshot.steps.filter((s) => s.status !== "unknown").length;

  // Only sections that have something in them are listed, so the rail never
  // sends somebody to an empty heading.
  const sections = [
    { id: "findings", label: "What it found" },
    ...(hasReport || embeddingData.length > 0 ? [{ id: "report", label: "Report" }] : []),
    { id: "quality", label: "Quality control" },
    { id: "how", label: "How it ran" },
    { id: "provenance", label: "Provenance" },
    ...(entries.length > 0 ? [{ id: "files", label: "Files" }] : []),
  ];

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <div className="doc">
        <div className="doc-body">
          <header className="doc-head">
            <h1>{id}</h1>
            <p className="subtle">
              <strong>{statusWord(snapshot.status)}</strong>
              {STATUS_WORDS[snapshot.status] && ` — ${STATUS_WORDS[snapshot.status].meaning}`}
              <br />
              started {formatTime(snapshot.started_at)}
              {snapshot.species ? ` · species ${snapshot.species}` : " · species not recorded"}
            </p>
          </header>

          {/* Ahead of the gate check: a run that halted has no pending gate to
              show, and a "waiting for your decision" panel that never
              appears is a worse silence than the one this replaces. */}
          {halted && (
            <div className="panel" data-tone="fail" data-testid="run-halted">
              <h2 style={{ marginTop: 0 }}>This run stopped without producing a result</h2>
              <p style={{ marginTop: 0 }}>{halted}</p>
              <p className="subtle" style={{ marginBottom: 0 }}>
                Every step before this one is intact. Resolve what it is asking for and resume:
                <br />
                <code>
                  python -m src.run --continue-from {id} --input &lt;same input&gt; --interactive
                </code>
              </p>
            </div>
          )}

          {/* The state of the run decides what comes first, because a run that
              wants something from a person wants it more than they want its
              findings — and it will wait for ever without saying so anywhere
              else. */}
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
                <h2 style={{ marginTop: 0 }}>Waiting for your decision</h2>
                <p style={{ marginTop: 0 }}>
                  <strong>{stepLabel(snapshot.pending_gate.step).title}</strong> —{" "}
                  {stepLabel(snapshot.pending_gate.step).what}
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

          {/* Only rendered for a run that is actually working. It is the one
              self-refreshing thing on the site, and it exists because
              `cellranger_count` goes quiet for tens of minutes and a working
              run used to be indistinguishable from a dead one. */}
          <Progress
            runId={id}
            initial={{
              status: snapshot.status,
              unfinished_step: snapshot.unfinished_step,
              current_step_elapsed_seconds: snapshot.current_step_elapsed_seconds,
              steps: snapshot.steps.map((s) => ({ step: s.step, status: s.status })),
            }}
            timings={timings?.steps ?? {}}
          />

          {snapshot.status === "interrupted" && (
            <div className="panel" data-tone="warn" data-testid="interrupted-panel">
              <h2 style={{ marginTop: 0 }}>This run stopped without finishing</h2>
              <p style={{ marginTop: 0 }}>
                It was inside{" "}
                <strong>
                  {snapshot.unfinished_step ? stepLabel(snapshot.unfinished_step).title : "a step"}
                </strong>{" "}
                <code>{snapshot.unfinished_step ?? ""}</code> when it last wrote anything
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

          <section id="findings">
            <h2>What it found</h2>
            <SummaryCards
              items={[
                { label: "Cells", value: snapshot.cells, title: "after QC filtering" },
                { label: "Clusters", value: snapshot.clusters, title: "from run_clustering" },
                { label: "Cell types", value: snapshot.cell_types, title: "from annotate_cells" },
                { label: "Steps recorded", value: snapshot.steps.length },
                { label: "Reviewer warnings", value: snapshot.warn_count },
                { label: "Reviewer failures", value: snapshot.fail_count },
              ]}
            />
            {snapshot.reused_steps.length > 0 && (
              <div className="panel">
                <h3 style={{ marginTop: 0 }}>Reused work</h3>
                <p className="subtle" style={{ marginTop: 0 }}>
                  These steps were not re-executed; their results came from an earlier run in the
                  same directory.
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
                  {snapshot.reused_steps.map((step) => (
                    <Badge key={step} tone="reused">
                      {stepLabel(step).title}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </section>

          {(hasReport || embeddingData.length > 0) && (
            <section id="report">
              <h2>Report</h2>
              {embeddingData.length > 0 && (
                <div className="panel">
                  <h3 style={{ marginTop: 0 }}>Interactive embedding</h3>
                  <p className="subtle" style={{ marginTop: 0 }}>
                    Choose the view and what to colour by. Hover cells, zoom, and rotate the 3D
                    views. Distances here are for looking at, not for measuring.
                  </p>
                  <EmbeddingViewer
                    runId={id}
                    dataArtifacts={embeddingData}
                    standaloneArtifacts={embeddings}
                  />
                </div>
              )}
              {hasReport && report?.content && (
                <ReportReader
                  content={report.content}
                  sourcePath={report.source_path}
                  runId={id}
                  figures={figures}
                  embeddings={embeddingArtifacts}
                />
              )}
            </section>
          )}

          <section id="quality">
            <h2>Quality control</h2>
            <Quality runId={id} steps={steps} artifacts={entries} />
          </section>

          <section id="how">
            <h2>How it ran</h2>
            <p className="subtle">
              Every step in the order the audit log recorded it — {done} of{" "}
              {snapshot.steps.length} have an outcome
              {snapshot.reused_steps.length > 0 && `, ${snapshot.reused_steps.length} reused`}.
              Select one to see what it ran with, what it said about its own result, and the
              reviewer&apos;s reasons.
            </p>
            <div className="panel">
              {steps ? (
                <WorkflowTimeline steps={steps} runId={id} />
              ) : (
                <p className="subtle" style={{ margin: 0 }}>
                  No step records were available for this run.
                </p>
              )}
            </div>
          </section>

          <section id="provenance">
            <h2>Provenance</h2>
            <Provenance provenance={provenance} />
          </section>

          {entries.length > 0 && (
            <section id="files">
              <h2>Files</h2>
              <Artifacts runId={id} artifacts={entries} />
            </section>
          )}
        </div>

        <Contents sections={sections} />
      </div>
    </RunShell>
  );
}

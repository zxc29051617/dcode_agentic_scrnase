import Link from "next/link";

import ArtifactFrame from "@/components/ArtifactFrame";
import Badge from "@/components/Badge";
import RunShell from "@/components/RunShell";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";
import { compareGroups, defaultCompareSelection } from "@/lib/compareRuns";
import { getRunSnapshot, listArtifacts, listRuns } from "@/lib/gateway";
import type { ArtifactEntry, RunSnapshot, RunSummary } from "@/lib/gatewayTypes";
import { formatCount, formatTime, runTone } from "@/lib/verdict";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function firstParam(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value[0] ?? null;
  return value ?? null;
}

function artifactHref(runId: string, artifactId: string): string {
  return `/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(artifactId)}`;
}

function artifactOf(artifacts: ArtifactEntry[] | null, kind: ArtifactEntry["kind"]): ArtifactEntry | null {
  return artifacts?.find((artifact) => artifact.kind === kind) ?? null;
}

function runOptionLabel(run: RunSummary): string {
  const parts = [run.scientific_run_id, run.status];
  if (run.started_at) parts.push(formatTime(run.started_at));
  const cells = formatCount(run.cells);
  if (cells) parts.push(`${cells} cells`);
  return parts.join(" · ");
}

function compareValue(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function comparisonRows(left: RunSnapshot, right: RunSnapshot) {
  return [
    ["Status", left.status, right.status],
    ["Started", formatTime(left.started_at), formatTime(right.started_at)],
    ["Last activity", formatTime(left.last_activity_at), formatTime(right.last_activity_at)],
    ["Species", compareValue(left.species), compareValue(right.species)],
    ["Cells", compareValue(formatCount(left.cells)), compareValue(formatCount(right.cells))],
    ["Clusters", compareValue(formatCount(left.clusters)), compareValue(formatCount(right.clusters))],
    ["Cell types", compareValue(formatCount(left.cell_types)), compareValue(formatCount(right.cell_types))],
    ["Steps recorded", String(left.steps.length), String(right.steps.length)],
    ["Reviewer warnings", String(left.warn_count), String(right.warn_count)],
    ["Reviewer failures", String(left.fail_count), String(right.fail_count)],
    ["Current step", compareValue(left.unfinished_step), compareValue(right.unfinished_step)],
    ["Current step started", formatTime(left.current_step_started_at), formatTime(right.current_step_started_at)],
    ["Pending gate", compareValue(left.pending_gate?.step), compareValue(right.pending_gate?.step)],
    ["Report recorded", left.has_report ? "yes" : "no", right.has_report ? "yes" : "no"],
  ] as const;
}

export default async function ComparePage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const runs = await listRuns();
  const groups = compareGroups(runs);
  const requestedInputRef = firstParam(params.input_ref);
  const requestedLeft = firstParam(params.left);
  const requestedRight = firstParam(params.right);
  const selection = defaultCompareSelection(groups, requestedInputRef, requestedLeft, requestedRight);
  const selectedGroup = selection.input_ref
    ? groups.find((group) => group.input_ref === selection.input_ref) ?? null
    : null;

  const [leftSnapshot, rightSnapshot, leftArtifacts, rightArtifacts] = await Promise.all([
    selection.left ? getRunSnapshot(selection.left) : Promise.resolve(null),
    selection.right ? getRunSnapshot(selection.right) : Promise.resolve(null),
    selection.left ? listArtifacts(selection.left) : Promise.resolve(null),
    selection.right ? listArtifacts(selection.right) : Promise.resolve(null),
  ]);

  const leftRun = selectedGroup?.runs.find((run) => run.scientific_run_id === selection.left) ?? null;
  const rightRun = selectedGroup?.runs.find((run) => run.scientific_run_id === selection.right) ?? null;
  const leftReportPdf = artifactOf(leftArtifacts, "report_pdf");
  const leftReportHtml = artifactOf(leftArtifacts, "report_html");
  const rightReportPdf = artifactOf(rightArtifacts, "report_pdf");
  const rightReportHtml = artifactOf(rightArtifacts, "report_html");
  const selectionLabel = selectedGroup
    ? `${selectedGroup.input_ref} — ${selectedGroup.runs.length} runs`
    : "No comparable runs yet";
  const assistantInstructions = selectedGroup && leftSnapshot && rightSnapshot
    ? `${READ_ONLY_INSTRUCTIONS}\n\nThis page compares two runs from the same input_ref: ${selectedGroup.input_ref}. Left run: ${leftSnapshot.scientific_run_id}. Right run: ${rightSnapshot.scientific_run_id}. When the user asks which is better, compare report content, QC, verdicts, provenance, step reuse, and whether any gate is still open. Name the better run first, then give the strongest evidence for that choice and the strongest caveat.`
    : `${READ_ONLY_INSTRUCTIONS}\n\nThis is a compare workspace. Ask the user to choose an input_ref that has at least two runs before comparing anything.`;

  return (
    <RunShell run={null} instructions={assistantInstructions} assistantDefaultOpen>
      <section className="stack">
        <div>
          <p className="subtle" style={{ marginBottom: "0.35rem" }}>
            Compare runs from the same input reference
          </p>
          <h1 style={{ marginTop: 0, marginBottom: "0.35rem" }}>Compare runs</h1>
          <p className="subtle" style={{ marginTop: 0 }}>
            The page only lists input_ref values that already have at least two runs, so the pair on screen always comes from the same source data.
          </p>
        </div>

        <form className="panel" method="get" style={{ display: "grid", gap: "0.9rem" }}>
          <div
            style={{
              display: "grid",
              gap: "0.9rem",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            }}
          >
            <label style={{ display: "grid", gap: "0.35rem" }}>
              <span className="nav-label">input_ref</span>
              <select name="input_ref" defaultValue={selection.input_ref ?? ""}>
                <option value="" disabled>
                  {groups.length > 0 ? "Choose an input_ref" : "No comparable runs yet"}
                </option>
                {groups.map((group) => (
                  <option key={group.input_ref} value={group.input_ref}>
                    {group.input_ref} ({group.runs.length})
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "grid", gap: "0.35rem" }}>
              <span className="nav-label">Left run</span>
              <select name="left" defaultValue={selection.left ?? ""} disabled={!selectedGroup}>
                <option value="" disabled>
                  {selectedGroup ? "Choose a run" : "Pick an input_ref first"}
                </option>
                {selectedGroup?.runs.map((run) => (
                  <option key={run.scientific_run_id} value={run.scientific_run_id}>
                    {runOptionLabel(run)}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "grid", gap: "0.35rem" }}>
              <span className="nav-label">Right run</span>
              <select name="right" defaultValue={selection.right ?? ""} disabled={!selectedGroup}>
                <option value="" disabled>
                  {selectedGroup ? "Choose a run" : "Pick an input_ref first"}
                </option>
                {selectedGroup?.runs.map((run) => (
                  <option key={run.scientific_run_id} value={run.scientific_run_id}>
                    {runOptionLabel(run)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="controls">
            <span className="subtle">{selectionLabel}</span>
            <span className="spacer" />
            <button type="submit" disabled={!selectedGroup}>
              Compare runs
            </button>
          </div>
        </form>

        {!selectedGroup ? (
          <div className="panel">
            <p style={{ margin: 0 }}>No input_ref has two or more recorded runs yet.</p>
          </div>
        ) : leftSnapshot && rightSnapshot && leftRun && rightRun ? (
          <>
            <section className="panel" style={{ display: "grid", gap: "0.9rem" }}>
              <div>
                <p className="subtle" style={{ marginBottom: "0.35rem" }}>
                  Difference summary
                </p>
                <h2 style={{ marginTop: 0, marginBottom: 0 }}>{selectedGroup.input_ref}</h2>
                <p className="subtle" style={{ marginTop: "0.35rem" }}>
                  {selectedGroup.runs.length} runs share this input_ref; the table below compares the two selected runs.
                </p>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left", padding: "0.5rem 0.25rem" }}>Field</th>
                      <th style={{ textAlign: "left", padding: "0.5rem 0.25rem" }}>{leftSnapshot.scientific_run_id}</th>
                      <th style={{ textAlign: "left", padding: "0.5rem 0.25rem" }}>{rightSnapshot.scientific_run_id}</th>
                      <th style={{ textAlign: "left", padding: "0.5rem 0.25rem" }}>Match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisonRows(leftSnapshot, rightSnapshot).map(([label, left, right]) => {
                      const same = left === right;
                      return (
                        <tr key={label}>
                          <td style={{ padding: "0.45rem 0.25rem", borderTop: "1px solid var(--line)" }}>
                            {label}
                          </td>
                          <td style={{ padding: "0.45rem 0.25rem", borderTop: "1px solid var(--line)" }}>
                            {left}
                          </td>
                          <td style={{ padding: "0.45rem 0.25rem", borderTop: "1px solid var(--line)" }}>
                            {right}
                          </td>
                          <td style={{ padding: "0.45rem 0.25rem", borderTop: "1px solid var(--line)" }}>
                            <Badge tone={same ? "reused" : "warn"}>{same ? "same" : "different"}</Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <section
              style={{
                display: "grid",
                gap: "1rem",
                gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
                alignItems: "start",
              }}
            >
              {[
                { label: "Left", snapshot: leftSnapshot, run: leftRun, artifacts: leftArtifacts, pdf: leftReportPdf, html: leftReportHtml },
                { label: "Right", snapshot: rightSnapshot, run: rightRun, artifacts: rightArtifacts, pdf: rightReportPdf, html: rightReportHtml },
              ].map((side) => {
                const reportArtifact = side.pdf && !side.pdf.too_large ? side.pdf : side.html ?? side.pdf;
                const reportUrl = reportArtifact ? artifactHref(side.run.scientific_run_id, reportArtifact.artifact_id) : null;
                return (
                  <section key={side.run.scientific_run_id} className="panel" style={{ display: "grid", gap: "0.9rem" }}>
                    <div style={{ display: "flex", alignItems: "start", gap: "0.75rem" }}>
                      <div>
                        <p className="subtle" style={{ margin: 0 }}>{side.label} run</p>
                        <h2 style={{ margin: 0 }}>
                          <Link href={`/runs/${encodeURIComponent(side.run.scientific_run_id)}`}>
                            {side.run.scientific_run_id}
                          </Link>
                        </h2>
                        <p className="subtle" style={{ margin: "0.35rem 0 0" }}>
                          input_ref <code>{selectedGroup.input_ref}</code>
                        </p>
                      </div>
                      <span className="spacer" />
                      <Badge tone={runTone(side.snapshot.status)}>{side.snapshot.status}</Badge>
                    </div>

                    <dl
                      style={{
                        display: "grid",
                        gridTemplateColumns: "auto 1fr",
                        gap: "0.35rem 0.75rem",
                        margin: 0,
                      }}
                    >
                      <dt className="subtle">Started</dt>
                      <dd style={{ margin: 0 }}>{formatTime(side.snapshot.started_at)}</dd>
                      <dt className="subtle">Last activity</dt>
                      <dd style={{ margin: 0 }}>{formatTime(side.snapshot.last_activity_at)}</dd>
                      <dt className="subtle">Cells</dt>
                      <dd style={{ margin: 0 }}>{compareValue(formatCount(side.snapshot.cells))}</dd>
                      <dt className="subtle">Clusters</dt>
                      <dd style={{ margin: 0 }}>{compareValue(formatCount(side.snapshot.clusters))}</dd>
                      <dt className="subtle">Cell types</dt>
                      <dd style={{ margin: 0 }}>{compareValue(formatCount(side.snapshot.cell_types))}</dd>
                      <dt className="subtle">Steps recorded</dt>
                      <dd style={{ margin: 0 }}>{side.snapshot.steps.length}</dd>
                      <dt className="subtle">Warnings</dt>
                      <dd style={{ margin: 0 }}>{side.snapshot.warn_count}</dd>
                      <dt className="subtle">Failures</dt>
                      <dd style={{ margin: 0 }}>{side.snapshot.fail_count}</dd>
                      <dt className="subtle">Current step</dt>
                      <dd style={{ margin: 0 }}>{compareValue(side.snapshot.unfinished_step)}</dd>
                    </dl>

                    <div className="controls" style={{ flexWrap: "wrap" }}>
                      <span className="subtle">
                        {reportArtifact ? reportArtifact.label : "No report artifact recorded"}
                      </span>
                      <span className="spacer" />
                      {reportUrl && (
                        <a className="nav-item" href={reportUrl} target="_blank" rel="noopener noreferrer">
                          Open report ↗
                        </a>
                      )}
                      {side.pdf && side.html && (
                        <a
                          className="nav-item"
                          href={artifactHref(side.run.scientific_run_id, side.html.artifact_id)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open HTML ↗
                        </a>
                      )}
                      <Link className="nav-item" href={`/runs/${encodeURIComponent(side.run.scientific_run_id)}`}>
                        Open run
                      </Link>
                    </div>

                    {reportArtifact ? (
                      reportArtifact.kind === "report_html" ? (
                        <ArtifactFrame runId={side.run.scientific_run_id} artifact={reportArtifact} height="65vh" />
                      ) : reportArtifact.too_large ? (
                        <div className="panel" data-tone="warn">
                          <p style={{ margin: 0 }}>
                            <code>{reportArtifact.name}</code> is too large to show in the browser.
                          </p>
                        </div>
                      ) : (
                        <iframe
                          src={reportUrl ?? undefined}
                          title={`${side.run.scientific_run_id} report`}
                          style={{
                            width: "100%",
                            height: "65vh",
                            border: "1px solid var(--line)",
                            borderRadius: "8px",
                            background: "var(--foreign-bg)",
                          }}
                        />
                      )
                    ) : (
                      <div className="panel" data-tone="warn">
                        <p style={{ margin: 0 }}>No report artifact was recorded for this run.</p>
                      </div>
                    )}
                  </section>
                );
              })}
            </section>
          </>
        ) : (
          <div className="panel">
            <p style={{ margin: 0 }}>The selected runs could not be loaded from the gateway.</p>
          </div>
        )}
      </section>
    </RunShell>
  );
}

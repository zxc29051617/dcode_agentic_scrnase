import Link from "next/link";

import Badge from "@/components/Badge";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";
import { compareGroups, defaultCompareSelection } from "@/lib/compareRuns";
import { getRunSnapshot, listArtifacts, listRuns } from "@/lib/gateway";
import type { ArtifactEntry, RunSnapshot, RunSummary } from "@/lib/gatewayTypes";
import { formatCount, formatTime, runTone } from "@/lib/verdict";

export const dynamic = "force-dynamic";

const COMPARE_ASSISTANT_SUGGESTIONS = [
  "Which run looks better overall?",
  "What are the biggest differences between these two runs?",
  "Which run has fewer warnings or failures?",
  "Summarize the provenance and step differences.",
];

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
type ComparisonRow = readonly [string, string, string];

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

function comparisonRows(left: RunSnapshot, right: RunSnapshot): ComparisonRow[] {
  return [
    ["Status", left.status, right.status],
    ["Started", formatTime(left.started_at), formatTime(right.started_at)],
    ["Last activity", formatTime(left.last_activity_at), formatTime(right.last_activity_at)],
    ["Species", left.species ?? "—", right.species ?? "—"],
    ["Cells", formatCount(left.cells) ?? "—", formatCount(right.cells) ?? "—"],
    ["Clusters", formatCount(left.clusters) ?? "—", formatCount(right.clusters) ?? "—"],
    ["Cell types", formatCount(left.cell_types) ?? "—", formatCount(right.cell_types) ?? "—"],
    ["Steps recorded", String(left.steps.length), String(right.steps.length)],
    ["Reviewer warnings", String(left.warn_count), String(right.warn_count)],
    ["Reviewer failures", String(left.fail_count), String(right.fail_count)],
    ["Current step", left.unfinished_step ?? "—", right.unfinished_step ?? "—"],
    ["Current step started", formatTime(left.current_step_started_at), formatTime(right.current_step_started_at)],
    ["Pending gate", left.pending_gate?.step ?? "—", right.pending_gate?.step ?? "—"],
    ["Report recorded", left.has_report ? "yes" : "no", right.has_report ? "yes" : "no"],
  ];
}

function reportArtifact(artifacts: ArtifactEntry[] | null): ArtifactEntry | null {
  const html = artifactOf(artifacts, "report_html");
  if (html && !html.too_large) return html;
  const pdf = artifactOf(artifacts, "report_pdf");
  if (pdf && !pdf.too_large) return pdf;
  return null;
}

function reportHref(runId: string, artifact: ArtifactEntry | null): string | null {
  return artifact ? artifactHref(runId, artifact.artifact_id) : null;
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
  const rows = leftSnapshot && rightSnapshot ? comparisonRows(leftSnapshot, rightSnapshot) : [];
  const sameCount = rows.filter(([, left, right]) => left === right).length;
  const diffCount = rows.length - sameCount;
  const selectionLabel = selectedGroup
    ? `${selectedGroup.input_ref} — ${selectedGroup.runs.length} runs`
    : "No comparable runs yet";

  const assistantInstructions = selectedGroup && leftSnapshot && rightSnapshot
    ? `${READ_ONLY_INSTRUCTIONS}\n\nThis compare workspace shows two runs from the same input_ref: ${selectedGroup.input_ref}. Left run: ${leftSnapshot.scientific_run_id}. Right run: ${rightSnapshot.scientific_run_id}. Answer from recorded summaries, report previews, provenance, and steps only. If the user asks which is better, start with the winner, then give the key differences as short bullets.`
    : `${READ_ONLY_INSTRUCTIONS}\n\nThis is a compare workspace. Ask the user to choose an input_ref that has at least two runs before comparing anything.`;

  return (
    <RunShell
      run={null}
      instructions={assistantInstructions}
      assistantDefaultOpen
      assistantTitle="Compare workspace"
      assistantInitialMessage="Ask which run looks better, what differs, or which QC signal is stronger."
      assistantSuggestions={COMPARE_ASSISTANT_SUGGESTIONS}
    >
      <section className="stack compare-page">
        <header className="compare-hero">
          <p className="subtle">Compare runs from the same input reference</p>
          <h1>Compare runs</h1>
          <p className="subtle">Choose one input_ref, then inspect two runs side by side.</p>
        </header>

        <form className="panel compare-selector" method="get">
          <div className="compare-selector-grid">
            <label className="compare-field">
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

            <label className="compare-field">
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

            <label className="compare-field">
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

          <div className="controls compare-selector-actions">
            <span className="subtle">{selectionLabel}</span>
            <span className="spacer" />
            <button type="submit" disabled={!selectedGroup}>
              Compare runs
            </button>
          </div>
        </form>

        {!selectedGroup ? (
          <div className="panel compare-empty">
            <p className="compare-empty-text">No input_ref has two or more recorded runs yet.</p>
          </div>
        ) : leftSnapshot && rightSnapshot && leftRun && rightRun ? (
          <>
            <SummaryCards
              items={[
                {
                  label: "Input ref",
                  value: selectedGroup.input_ref,
                  title: "The shared source data for both runs.",
                },
                {
                  label: "Left run",
                  value: `${leftSnapshot.scientific_run_id} · ${leftSnapshot.status}`,
                  title: `Started ${formatTime(leftSnapshot.started_at)}`,
                },
                {
                  label: "Right run",
                  value: `${rightSnapshot.scientific_run_id} · ${rightSnapshot.status}`,
                  title: `Started ${formatTime(rightSnapshot.started_at)}`,
                },
                {
                  label: "Matching fields",
                  value: sameCount,
                  title: "Comparison fields that match exactly.",
                },
                {
                  label: "Different fields",
                  value: diffCount,
                  title: "Comparison fields that differ.",
                },
              ]}
            />

            <section className="compare-columns">
              {[
                { label: "Left", snapshot: leftSnapshot, run: leftRun, artifacts: leftArtifacts },
                { label: "Right", snapshot: rightSnapshot, run: rightRun, artifacts: rightArtifacts },
              ].map((side) => {
                const artifact = reportArtifact(side.artifacts);
                const href = reportHref(side.run.scientific_run_id, artifact);
                const previewLabel = artifact ? artifact.label : "No report artifact recorded";

                return (
                  <article key={side.run.scientific_run_id} className="panel compare-column">
                    <div className="compare-column-head">
                      <div>
                        <p className="subtle compare-run-kicker">{side.label} run</p>
                        <h2 className="compare-run-title">
                          <Link href={`/runs/${encodeURIComponent(side.run.scientific_run_id)}`}>
                            {side.run.scientific_run_id}
                          </Link>
                        </h2>
                        <p className="subtle compare-run-subtitle">
                          {side.snapshot.status} · started {formatTime(side.snapshot.started_at)}
                        </p>
                      </div>
                      <span className="spacer" />
                      <Badge tone={runTone(side.snapshot.status)}>{side.snapshot.status}</Badge>
                    </div>

                    <dl className="compare-run-meta">
                      <div>
                        <dt className="subtle">Cells</dt>
                        <dd>{formatCount(side.snapshot.cells) ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="subtle">Clusters</dt>
                        <dd>{formatCount(side.snapshot.clusters) ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="subtle">Cell types</dt>
                        <dd>{formatCount(side.snapshot.cell_types) ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="subtle">Current step</dt>
                        <dd>{side.snapshot.unfinished_step ?? "—"}</dd>
                      </div>
                    </dl>

                    <div className="controls compare-run-links">
                      <span className="subtle">{previewLabel}</span>
                      <span className="spacer" />
                      {href && (
                        <a className="nav-item" href={href} target="_blank" rel="noopener noreferrer">
                          Open report ↗
                        </a>
                      )}
                      <Link className="nav-item" href={`/runs/${encodeURIComponent(side.run.scientific_run_id)}`}>
                        Open run
                      </Link>
                    </div>

                    {artifact ? (
                      <details className="compare-run-preview">
                        <summary>Report preview</summary>
                        <div className="compare-run-preview-body">
                          {artifact.too_large ? (
                            <p className="subtle compare-preview-note">
                              <code>{artifact.name}</code> is too large to preview here.
                            </p>
                          ) : href ? (
                            <iframe
                              className="compare-preview-frame"
                              src={href}
                              title={`${side.run.scientific_run_id} report`}
                              loading="lazy"
                              referrerPolicy="no-referrer"
                              sandbox={artifact.kind === "report_html" ? "allow-scripts" : undefined}
                            />
                          ) : null}
                        </div>
                      </details>
                    ) : (
                      <p className="subtle compare-preview-note">No report artifact was recorded for this run.</p>
                    )}
                  </article>
                );
              })}
            </section>
          </>
        ) : (
          <div className="panel compare-empty">
            <p className="compare-empty-text">The selected runs could not be loaded from the gateway.</p>
          </div>
        )}
      </section>
    </RunShell>
  );
}

"use client";

import { useState } from "react";
import Badge from "@/components/Badge";
import UpstreamQC from "@/components/UpstreamQC";
import { stepTone, stepToneLabel } from "@/lib/verdict";
import type { StepRecord } from "@/lib/gatewayTypes";

/**
 * The run's steps in recorded order, each expandable.
 *
 * Collapsed, a row is the one thing a reader scans for: did this step pass.
 * Expanded, it shows the judge's own reasons, the step's warnings and errors,
 * and its recorded metrics as labelled rows — not a pasted JSON blob, which
 * is unreadable and hides exactly the fields somebody is looking for.
 */
export default function WorkflowTimeline({
  steps,
  runId,
}: {
  steps: StepRecord[];
  /** Needed to build figure URLs, which go through this app's own proxy so the
   *  browser never learns the gateway's address. */
  runId: string;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (steps.length === 0) {
    return <p className="subtle">No steps were recorded in this run&apos;s audit log.</p>;
  }

  return (
    <div className="timeline">
      {steps.map((step) => {
        const tone = stepTone(step.status, step.verdict?.verdict);
        const expanded = open === step.step;
        const problems = step.output_summary.warnings.length + step.output_summary.errors.length;
        // Read defensively: the gateway is a separate service on its own
        // deploy cycle, and these three fields postdate this component. An
        // older gateway simply omits them, which must render as "this step
        // recorded none" rather than crashing the whole timeline.
        const notes = step.notes ?? [];
        const settings = step.settings ?? {};
        const figures = step.figures ?? [];
        return (
          <div key={step.step}>
            <button
              className="tl-row"
              aria-expanded={expanded}
              onClick={() => setOpen(expanded ? null : step.step)}
            >
              <span className="tl-dot" data-tone={tone} />
              <span className="tl-name">{step.step}</span>
              <span style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                {problems > 0 && (
                  <span className="subtle">
                    {problems} note{problems === 1 ? "" : "s"}
                  </span>
                )}
                <Badge tone={tone}>{stepToneLabel(step.status, step.verdict?.verdict)}</Badge>
              </span>
            </button>

            {expanded && (
              <div className="tl-detail">
                <dl className="kv">
                  <dt>status</dt>
                  <dd>{step.status}</dd>
                  {step.verdict && (
                    <>
                      <dt>judge</dt>
                      <dd>
                        {step.verdict.verdict} · score {step.verdict.score}
                      </dd>
                    </>
                  )}
                </dl>

                {step.verdict?.reasons?.length ? (
                  <>
                    <p className="subtle" style={{ margin: "0.6rem 0 0.2rem" }}>
                      judge reasons
                    </p>
                    <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                      {step.verdict.reasons.map((reason, i) => (
                        <li key={i}>{reason}</li>
                      ))}
                    </ul>
                  </>
                ) : null}

                {step.output_summary.errors.length > 0 && (
                  <>
                    <p className="subtle" style={{ margin: "0.6rem 0 0.2rem" }}>
                      errors
                    </p>
                    <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                      {step.output_summary.errors.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </>
                )}

                {step.output_summary.warnings.length > 0 && (
                  <>
                    <p className="subtle" style={{ margin: "0.6rem 0 0.2rem" }}>
                      warnings
                    </p>
                    <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                      {step.output_summary.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </>
                )}

                {/* The step's own reservations, above the numbers rather than
                    below them. A judge can return `pass` on a step that
                    recorded a doubt — the judge is asked whether the step ran
                    soundly, and a cluster of 8 cells is a sound run of an
                    unsound-looking result. Burying that under the metrics
                    would be filing it where nobody reads. */}
                {notes.length > 0 && (
                  <div data-testid={`notes-${step.step}`}>
                    <p className="subtle" style={{ margin: "0.6rem 0 0.2rem" }}>
                      what this step said about its own result
                    </p>
                    <ul style={{ margin: "0 0 0.4rem" }}>
                      {notes.map((note, i) => (
                        <li key={i}>{note}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* FastQC / Cell Ranger publish their own HTML reports; the
                    numbers out of them are already recorded, so they are
                    shown here rather than sent to another page. */}
                {step.upstream_detail && <UpstreamQC detail={step.upstream_detail} />}

                <Metrics metrics={step.output_summary.metrics} />

                <Settings settings={settings} step={step.step} />

                <Figures figures={figures} runId={runId} step={step.step} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Recorded metrics as labelled rows. Nested values are shown compactly. */
function Metrics({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) {
    return (
      <p className="subtle" style={{ margin: "0.6rem 0 0" }}>
        No metrics recorded for this step.
      </p>
    );
  }
  return (
    <>
      <p className="subtle" style={{ margin: "0.6rem 0 0.2rem" }}>
        recorded metrics
      </p>
      <dl className="kv">
        {entries.map(([key, value]) => (
          <div key={key} style={{ display: "contents" }}>
            <dt>{key}</dt>
            <dd className="num">{render(value)}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length <= 8 ? value.join(", ") : `${value.length} values`;
  const keys = Object.keys(value as object);
  return keys.length <= 6 ? JSON.stringify(value) : `${keys.length} entries`;
}


/**
 * How the step ran — the settings, thresholds and choices behind its numbers.
 *
 * `docs/report_contract.md` calls this the tier almost no published pipeline
 * provides: "who decided what, and can it be rerun". It was recorded from the
 * first run and shown nowhere, so the app could say a step passed and not what
 * it passed with.
 *
 * Rendered as labelled rows rather than a JSON dump for the same reason
 * `Metrics` is: a blob is unreadable and hides the one field somebody came to
 * find. Nested blocks collapse to a summary line with their own disclosure,
 * because `per_cluster` on a 15-cluster run would otherwise bury everything
 * after it.
 */
function Settings({
  settings,
  step,
}: {
  settings: NonNullable<StepRecord["settings"]>;
  step: string;
}) {
  const entries = Object.entries(settings);
  if (entries.length === 0) return null;
  return (
    <div data-testid={`settings-${step}`}>
      <p className="subtle" style={{ margin: "0.8rem 0 0.2rem" }}>
        how this step ran
      </p>
      <dl className="kv">
        {entries.map(([key, value]) => {
          const nested = value !== null && typeof value === "object";
          return (
            <div key={key} style={{ display: "contents" }}>
              <dt>{key}</dt>
              <dd>
                {nested ? (
                  <details>
                    <summary className="subtle">
                      {Array.isArray(value)
                        ? `${value.length} entries`
                        : `${Object.keys(value as object).length} fields`}
                    </summary>
                    <dl className="kv" style={{ marginTop: "0.35rem" }}>
                      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
                        <div key={k} style={{ display: "contents" }}>
                          <dt>{k}</dt>
                          <dd className="num">{render(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                ) : (
                  <span className="num">{render(value)}</span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

/**
 * The figures this step's numbers produced.
 *
 * They all live in `build_report/figures/` under report-section names — `a4`,
 * `m3` — so without this a person looking at `detect_doublets` has to already
 * know its plot is called `a4_doublets`. The mapping is the gateway's, in one
 * place; this only renders what it attributed.
 *
 * Served through `/api/artifacts/...`, this app's own proxy, by opaque id. The
 * browser never learns the gateway's address and never names a file.
 */
function Figures({
  figures,
  runId,
  step,
}: {
  figures: NonNullable<StepRecord["figures"]>;
  runId: string;
  step: string;
}) {
  if (figures.length === 0) return null;
  return (
    <div data-testid={`figures-${step}`}>
      <p className="subtle" style={{ margin: "0.8rem 0 0.3rem" }}>
        figures from this step
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        {figures.map((figure) => (
          <figure key={figure.artifact_id} style={{ margin: 0, maxWidth: "min(420px, 100%)" }}>
            <a
              href={`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(figure.artifact_id)}`}
              target="_blank"
              rel="noreferrer"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(figure.artifact_id)}`}
                alt={`${step} — ${figure.name}`}
                loading="lazy"
                style={{
                  width: "100%",
                  height: "auto",
                  border: "1px solid var(--line)",
                  borderRadius: "4px",
                  background: "var(--panel)",
                }}
              />
            </a>
            {/* The filename, not the gateway's `label` — that is the same
                "Report figure" for every figure in the run, which tells a
                reader nothing about which one they are looking at. The name
                carries the report section it belongs to (`a4_doublets`), and
                the section codes are what `docs/report_contract.md` uses. */}
            <figcaption className="subtle" style={{ fontSize: "0.78rem", marginTop: "0.25rem" }}>
              <code>{figure.name}</code>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

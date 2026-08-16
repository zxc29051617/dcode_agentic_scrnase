"use client";

import { useState } from "react";
import Badge from "@/components/Badge";
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
export default function WorkflowTimeline({ steps }: { steps: StepRecord[] }) {
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

                <Metrics metrics={step.output_summary.metrics} />
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

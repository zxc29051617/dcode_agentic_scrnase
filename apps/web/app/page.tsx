import Link from "next/link";
import RunShell from "@/components/RunShell";
import Badge from "@/components/Badge";
import { listRuns } from "@/lib/gateway";
import { controllerConfigured } from "@/lib/controller";
import { statusWord, stepLabel, STATUS_WORDS } from "@/lib/stepLabels";
import { runTone } from "@/lib/verdict";
import type { RunSummary } from "@/lib/gatewayTypes";

export const dynamic = "force-dynamic";

/**
 * The first screen, and the one that decides whether somebody can use this.
 *
 * It used to be `redirect("/runs")` — a table of past runs. That is the right
 * page for somebody who already has runs and knows what they are looking for,
 * and the wrong first thing for everybody else: a person opening this for the
 * first time saw an empty table, and the only action they wanted was a link in
 * the navigation bar rather than anything on the page.
 *
 * So this page answers three questions, in the order they are actually asked:
 *
 *   1. does anything need me right now?   — a paused run is the most urgent
 *                                            thing this system produces, and
 *                                            it waits indefinitely and
 *                                            silently until someone looks
 *   2. what do I do?                       — one primary action, not six
 *   3. what happened before?               — recent runs, compact, below
 *
 * A run at a gate is put first because of what it costs to miss: the run is
 * suspended, holding its checkpoint, doing nothing, and nothing anywhere else
 * says so. The runs table sorts by date, so a week-old run waiting for a
 * decision sinks below finished ones.
 */
export default async function Home() {
  let runs: RunSummary[] = [];
  let error: string | null = null;
  try {
    runs = await listRuns();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const canStart = controllerConfigured();
  // `interrupted` is here with `needs_review` because both mean a person has
  // to do something, and neither will resolve on its own. `failed` is not:
  // there is nothing to decide, only something to read.
  const waiting = runs.filter((r) => r.status === "needs_review");
  const stalled = runs.filter((r) => r.status === "interrupted" || r.status === "failed");
  const recent = runs.slice(0, 5);

  return (
    <RunShell run={null}>
      <h1>Single-cell RNA-seq analysis</h1>
      <p className="lede">
        A fixed pipeline with a person in the loop. Deterministic code does the analysis; a model
        scores each step and the run stops for you wherever a choice is genuinely yours.{" "}
        <strong>No model ever writes an analysis result.</strong>
      </p>

      {/* 1. Does anything need me? */}
      {waiting.length > 0 && (
        <section className="panel" data-tone="warn" data-testid="home-waiting">
          <h2 style={{ marginTop: 0 }}>
            {waiting.length === 1 ? "A run is waiting for you" : `${waiting.length} runs are waiting for you`}
          </h2>
          <p className="subtle" style={{ marginTop: 0 }}>
            {STATUS_WORDS.needs_review.meaning} It will wait indefinitely — nothing times out.
          </p>
          <ul className="plain-list">
            {waiting.map((run) => (
              <li key={run.scientific_run_id}>
                <Link href={`/runs/${encodeURIComponent(run.scientific_run_id)}`}>
                  <code>{run.scientific_run_id}</code>
                </Link>
                {/* The step the gate is about, in the words the gate itself
                    will use — so the row and the page it leads to agree. */}
                {run.pending_gate_step && (
                  <span className="subtle"> — {stepLabel(run.pending_gate_step).title}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 2. What do I do? One action, and it is the one this tool exists for. */}
      <section className="panel hero">
        {canStart ? (
          <>
            <h2 style={{ marginTop: 0 }}>Start an analysis</h2>
            <p className="subtle">
              Describe what you have and what you want to know. The next page shows exactly what
              would run, and how long it takes, before anything starts.
            </p>
            <Link className="btn-primary" href="/analysis/new" data-testid="home-start">
              New analysis →
            </Link>
            <p className="subtle" style={{ marginBottom: 0, marginTop: "0.9rem" }}>
              First time? A count matrix takes a few minutes. Starting from raw sequencing reads
              takes closer to an hour, most of it in one step that goes quiet while it works.
            </p>
          </>
        ) : (
          <>
            <h2 style={{ marginTop: 0 }}>This deployment can read runs, not start them</h2>
            <p className="subtle" style={{ marginBottom: 0 }}>
              <code>ANALYSIS_CONTROLLER_URL</code> is unset, so there is no write side to talk to.
              Runs can still be started from a terminal:{" "}
              <code>python -m src.run --input &lt;path&gt; --species human</code>
            </p>
          </>
        )}
      </section>

      {stalled.length > 0 && (
        <section className="panel" data-testid="home-stalled">
          <h2 style={{ marginTop: 0 }}>Stopped without finishing</h2>
          <ul className="plain-list">
            {stalled.map((run) => (
              <li key={run.scientific_run_id}>
                <Link href={`/runs/${encodeURIComponent(run.scientific_run_id)}`}>
                  <code>{run.scientific_run_id}</code>
                </Link>{" "}
                <Badge tone={runTone(run.status)}>{statusWord(run.status)}</Badge>
                {run.unfinished_step && (
                  <span className="subtle">
                    {" "}— stopped inside {stepLabel(run.unfinished_step).title}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 3. What happened before? */}
      {error ? (
        <section className="panel" data-tone="fail">
          <h2 style={{ marginTop: 0 }}>Cannot reach the gateway</h2>
          <p style={{ margin: 0 }}>{error}</p>
        </section>
      ) : runs.length === 0 ? (
        <section className="panel" data-testid="home-empty">
          <h2 style={{ marginTop: 0 }}>No runs yet</h2>
          <p className="subtle" style={{ marginTop: 0 }}>
            When you start one it appears here. Along the way it will pause and ask you things —
            which cells to keep, which cell-type model to use, which tissue to check the labels
            against. Those are the decisions the pipeline will not make on your behalf, because a
            wrong one produces a confident answer rather than an error.
          </p>
        </section>
      ) : (
        <section className="panel">
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
            <h2 style={{ marginTop: 0 }}>Recent runs</h2>
            <Link href="/runs" className="subtle">
              all {runs.length} →
            </Link>
          </div>
          <ul className="plain-list">
            {recent.map((run) => (
              <li key={run.scientific_run_id}>
                <Link href={`/runs/${encodeURIComponent(run.scientific_run_id)}`}>
                  <code>{run.scientific_run_id}</code>
                </Link>{" "}
                <Badge tone={runTone(run.status)}>{statusWord(run.status)}</Badge>
                {run.cells != null && <span className="subtle"> · {run.cells.toLocaleString()} cells</span>}
                {run.cell_types != null && <span className="subtle"> · {run.cell_types} cell types</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </RunShell>
  );
}

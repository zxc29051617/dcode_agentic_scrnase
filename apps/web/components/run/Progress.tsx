"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { stepLabel } from "@/lib/stepLabels";
import { humanDuration, expectedDuration } from "@/lib/duration";
import { applyPoll, shouldPoll, type LiveProgress as Live } from "@/lib/runProgress";

type Timing = { n: number; median_seconds: number; min_seconds: number; max_seconds: number };

/**
 * What a running analysis is doing right now, and whether that is normal.
 *
 * This is the only thing on the site that refreshes itself, and it exists
 * because of one specific failure: `cellranger_count` runs for tens of minutes
 * and writes nothing while it does. Every person who has watched it has
 * concluded the run had broken, and the page gave them no way to tell — a
 * working run and a dead one rendered identically.
 *
 * Three facts answer it, and all three come from measurement rather than from
 * a sentence somebody wrote:
 *
 *   which step        `unfinished_step`, in the words the timeline uses
 *   for how long      `current_step_elapsed_seconds`, from its own audit pair
 *   is that normal    `/v1/step-timings`, drawn from this machine's finished
 *                     runs and withheld entirely when there are too few
 *
 * The third is the load-bearing one. A hardcoded "about 30 minutes" is wrong
 * the first time somebody runs a 10k-cell library, and being wrong here sends
 * a person to investigate a run that is fine — or, worse, reassures them about
 * one that is not.
 *
 * ## Why polling
 *
 * There is no event stream, and adding one for this would be building a
 * transport to solve a problem a 15-second GET already solves. It is stated as
 * polling in the UI and in `docs/copilotkit_product_architecture.md` §3.3
 * rather than dressed up. Polling stops when the run is no longer running, so
 * a finished run costs nothing.
 *
 * ## Why it refreshes the page and not only itself
 *
 * A status change is the moment an entire section of the document appears or
 * disappears — the gate card, the halt notice, the report — and all of those
 * are server-rendered. Refreshing only this panel meant a run that reached a
 * human gate showed the person "Running now", then nothing, and no sign
 * anywhere that it was now waiting on them indefinitely. The one state this
 * product exists to make visible was the one state the page could not reach
 * without a manual reload.
 *
 * `router.refresh()` re-runs the server component, so the decision about what
 * the page should now contain stays where every other such decision is —
 * `app/runs/[id]/page.tsx`, reading the gateway and the controller. This
 * component decides only *when* to ask again, never what the answer means.
 */
const POLL_MS = 15_000;

export default function Progress({
  runId,
  initial,
  timings,
}: {
  runId: string;
  initial: Live;
  timings: Record<string, Timing>;
}) {
  const router = useRouter();
  const [live, setLive] = useState<Live>(initial);

  useEffect(() => {
    if (!shouldPoll(live.status)) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/live`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const incoming = (await res.json()) as Live;
        if (cancelled) return;
        const outcome = applyPoll(live.status, incoming);
        setLive(outcome.live);
        // The status moved, so the rest of the page is describing a run that
        // no longer exists in that state. Asking the server again is what puts
        // the gate card — or the halt notice, or the report — on the screen
        // without anybody reloading.
        if (outcome.refreshPage) router.refresh();
      } catch {
        // A failed poll is not worth telling anybody about: the next one is
        // fifteen seconds away, and an error banner that appears because a
        // laptop slept is worse than a number that is briefly stale.
      }
    };
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [runId, live.status, router]);

  if (!shouldPoll(live.status)) return null;

  const step = live.unfinished_step;
  const label = step ? stepLabel(step) : null;
  const elapsed = humanDuration(live.current_step_elapsed_seconds);
  const expected = step ? expectedDuration(timings[step]) : null;
  const timing = step ? timings[step] : undefined;
  const overdue =
    timing && live.current_step_elapsed_seconds != null
      ? live.current_step_elapsed_seconds > timing.max_seconds * 1.5
      : false;

  const done = live.steps.filter((s) => s.status !== "unknown").length;
  const remaining = Object.entries(timings)
    .filter(([name]) => !live.steps.some((s) => s.step === name && s.status !== "unknown"))
    .reduce((total, [, t]) => total + t.median_seconds, 0);

  return (
    <div className="panel" data-tone={overdue ? "warn" : undefined} data-testid="run-progress">
      {/* `.working` animates the ellipsis, so the heading itself says the run
          is alive. A spinner would say "wait"; this needs to say "still
          working", and it is quieter on a page somebody leaves open for half
          an hour. See the Uiverse note in globals.css. */}
      <h2 style={{ marginTop: 0 }}>
        {live.status === "queued" ? "Queued" : <span className="working">Running now</span>}
      </h2>

      {label ? (
        <>
          <p style={{ marginTop: 0, marginBottom: "0.3rem" }}>
            <strong>{label.title}</strong> <code className="tl-id">{step}</code>
          </p>
          <p className="subtle" style={{ marginTop: 0 }}>
            {label.what}
          </p>
          <p style={{ marginBottom: 0 }}>
            {elapsed ? `${elapsed} so far` : "just started"}
            {expected && <span className="subtle"> · {expected} on this machine</span>}
            {!expected && (
              <span className="subtle">
                {" "}
                · no measured expectation yet — this machine has not finished enough runs
              </span>
            )}
          </p>
          {overdue && (
            <p style={{ marginBottom: 0 }}>
              <strong>This is longer than any finished run took.</strong> It may still be working —
              a bigger library takes longer, and nothing here has timed out. If the process is
              gone, the run will be marked as stopped once it has been quiet long enough.
            </p>
          )}
        </>
      ) : (
        <p className="subtle" style={{ marginTop: 0, marginBottom: 0 }}>
          Between steps. Nothing is open in the audit log right now.
        </p>
      )}

      <p className="subtle" style={{ marginBottom: 0, marginTop: "0.8rem" }}>
        {done} of {live.steps.length} recorded steps have an outcome
        {remaining > 0 && ` · roughly ${humanDuration(remaining)} of measured work left`}. This
        panel refreshes every {POLL_MS / 1000} seconds.
      </p>
    </div>
  );
}

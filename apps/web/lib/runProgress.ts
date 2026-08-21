/**
 * What a progress poll means for the page around it.
 *
 * The progress panel is the only self-refreshing thing on the site, and it was
 * refreshing only itself. That left one specific gap, and it is the gap this
 * whole product exists to close: a run reaches a human gate, the gateway's
 * status moves from `running` to `needs_review`, the panel stops polling and
 * renders nothing — and the "waiting for your decision" card never appears,
 * because it is server-rendered and nothing asked the server again.
 *
 * What a person saw was the run apparently working, then a blank space, and no
 * indication that it was now waiting on them indefinitely. A manual reload was
 * the only way to find out.
 *
 * So a poll answers three questions rather than one, and they are separate
 * because they have different answers: what the panel should now show, whether
 * the *rest* of the page is still describing something true, and whether there
 * is any point asking again.
 *
 * Kept out of the component so it can be tested without a DOM. `apps/web/tests`
 * runs under `node --experimental-strip-types`, which removes type annotations
 * and does not transform JSX, so a `.tsx` file cannot be imported by a test at
 * all. A predicate that decides whether a person ever learns their run is
 * waiting is worth more than the convenience of keeping it inline.
 */

export type LiveProgress = {
  status: string;
  unfinished_step: string | null;
  current_step_elapsed_seconds: number | null;
  steps: { step: string; status: string }[];
};

/**
 * The statuses that are worth asking about again.
 *
 * Every other status is either terminal or waiting on a person, and in both
 * cases the next change comes from something this panel cannot observe.
 */
export const POLLED_STATUSES: readonly string[] = ["running", "queued"];

export function shouldPoll(status: string): boolean {
  return POLLED_STATUSES.includes(status);
}

export type PollOutcome = {
  /** What the panel shows from here. */
  live: LiveProgress;
  /**
   * Whether the server-rendered page is now stale and must be re-fetched.
   *
   * True exactly when the status moved. Deliberately not "when anything moved":
   * a step finishing changes the timeline, but the timeline being fifteen
   * seconds behind costs nothing, while re-rendering the whole document every
   * step boundary re-reads the snapshot, the steps, the artifacts, the report
   * and the provenance. A status change is the one movement that adds or
   * removes an entire section — the gate card, the halt notice, the report.
   */
  refreshPage: boolean;
  /** Whether to schedule another poll. */
  keepPolling: boolean;
};

/**
 * `currentStatus` rather than the whole previous state, because the status is
 * the only part of it this decision reads — and because the caller is a React
 * effect whose dependency list then names a string that changes rarely instead
 * of an object that is new on every poll. Passing the object would rebuild the
 * interval fifteen seconds at a time, or capture a stale one; naming exactly
 * what is used avoids having to choose.
 */
export function applyPoll(currentStatus: string, incoming: LiveProgress): PollOutcome {
  return {
    live: incoming,
    refreshPage: currentStatus !== incoming.status,
    keepPolling: shouldPoll(incoming.status),
  };
}

/**
 * Unit tests for the run page's progress panel, and when it must ask the server again.
 *
 * Run with:
 *     npm run test:unit
 *
 * The logic under test lives in `lib/` rather than inside the component for a
 * reason worth stating: this runner is `node --experimental-strip-types`, which
 * removes type annotations and does not transform JSX, so a `.tsx` file cannot
 * be imported here at all. Extracting the decision is what makes it testable
 * without adding a browser, a renderer or a build step to the unit suite.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { applyPoll, shouldPoll, type LiveProgress } from "../lib/runProgress.ts";

// --- Progress: a run that reaches a gate must not leave the page behind ------
//
// The failure: the panel refreshed itself and nothing else. A run moved from
// `running` to `needs_review`, the panel stopped polling and rendered null, and
// the server-rendered "waiting for your decision" card never appeared. What a
// person saw was a working run, then a blank space, and no indication that it
// was now waiting on them indefinitely.

function live(status: string, step: string | null = "run_pca"): LiveProgress {
  return {
    status,
    unfinished_step: step,
    current_step_elapsed_seconds: 12,
    steps: [{ step: "run_pca", status: "ok" }],
  };
}

test("a run still running refreshes nothing and keeps polling", () => {
  const outcome = applyPoll("running", live("running"));
  assert.equal(outcome.refreshPage, false);
  assert.equal(outcome.keepPolling, true);
});

test("reaching a human gate refreshes the page and stops polling", () => {
  // The one this exists for. `needs_review` is when the gate card has to
  // appear, and it is server-rendered, so the page must be asked again.
  const outcome = applyPoll("running", live("needs_review", null));
  assert.equal(outcome.refreshPage, true);
  assert.equal(outcome.keepPolling, false);
});

test("finishing refreshes the page so the report appears", () => {
  const outcome = applyPoll("running", live("completed", null));
  assert.equal(outcome.refreshPage, true);
  assert.equal(outcome.keepPolling, false);
});

test("going quiet long enough to be called interrupted refreshes the page", () => {
  const outcome = applyPoll("running", live("interrupted"));
  assert.equal(outcome.refreshPage, true);
  assert.equal(outcome.keepPolling, false);
});

test("queued becoming running refreshes, because the page said queued", () => {
  const outcome = applyPoll("queued", live("running"));
  assert.equal(outcome.refreshPage, true);
  assert.equal(outcome.keepPolling, true);
});

test("a step finishing inside one status does not re-fetch the document", () => {
  // Deliberate: re-rendering the whole page every step boundary would re-read
  // the snapshot, steps, artifacts, report and provenance for a timeline that
  // costs nothing by being fifteen seconds behind.
  const outcome = applyPoll("running", live("running", "run_umap"));
  assert.equal(outcome.refreshPage, false);
  assert.equal(outcome.live.unfinished_step, "run_umap");
});

test("only running and queued are worth asking about again", () => {
  assert.equal(shouldPoll("running"), true);
  assert.equal(shouldPoll("queued"), true);
  for (const terminal of ["needs_review", "completed", "failed", "interrupted", "unknown"]) {
    assert.equal(shouldPoll(terminal), false, `${terminal} must not be polled`);
  }
});

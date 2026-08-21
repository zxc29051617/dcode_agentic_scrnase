/**
 * Unit tests for the run list's human-readable status labels.
 *
 * Run with:
 *     npm run test:unit
 *
 * The list used to show the executor's raw status strings, which makes sense in
 * the gateway and the logs but not on a page a person scans for "waiting for
 * you" versus "stopped unexpectedly". This keeps the web vocabulary aligned
 * with the shared status map.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { STATUS_WORDS, statusWord } from "../lib/stepLabels.ts";
import { compareGroups, defaultCompareSelection, sortRunsByRecency } from "../lib/compareRuns.ts";

test("the run list uses the shared human-readable status vocabulary", () => {
  assert.equal(statusWord("needs_review"), STATUS_WORDS.needs_review.word);
  assert.equal(statusWord("interrupted"), STATUS_WORDS.interrupted.word);
  assert.equal(statusWord("completed"), STATUS_WORDS.completed.word);
  assert.equal(statusWord("queued"), STATUS_WORDS.queued.word);
});

test("compare helpers group runs by input_ref and pick the newest pair", () => {
  const runs = [
    { scientific_run_id: "run-c", input_ref: "dataset:a", started_at: "2026-01-03T00:00:00Z" },
    { scientific_run_id: "run-a", input_ref: "dataset:a", started_at: "2026-01-01T00:00:00Z" },
    { scientific_run_id: "run-b", input_ref: "dataset:a", started_at: "2026-01-02T00:00:00Z" },
    { scientific_run_id: "run-d", input_ref: "dataset:b", started_at: "2026-01-04T00:00:00Z" },
    { scientific_run_id: "run-e", input_ref: null, started_at: "2026-01-05T00:00:00Z" },
  ] as never;

  const groups = compareGroups(runs);
  assert.deepEqual(groups.map((group) => group.input_ref), ["dataset:a"]);
  assert.deepEqual(groups[0].runs.map((run) => run.scientific_run_id), ["run-c", "run-b", "run-a"]);
  assert.deepEqual(defaultCompareSelection(groups, null, null, null), {
    input_ref: "dataset:a",
    left: "run-c",
    right: "run-b",
  });
  assert.deepEqual(sortRunsByRecency(runs).map((run) => run.scientific_run_id), [
    "run-e",
    "run-d",
    "run-c",
    "run-b",
    "run-a",
  ]);
});

/**
 * Unit tests for the compare-page run selection helpers.
 *
 * Run with:
 *     npm run test:unit
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  compareGroups,
  defaultCompareSelection,
  sortRunsByRecency,
} from "../lib/compareRuns.ts";

const runs = [
  { scientific_run_id: "run-c", input_ref: "dataset:a", started_at: "2026-01-03T00:00:00Z" },
  { scientific_run_id: "run-a", input_ref: "dataset:a", started_at: "2026-01-01T00:00:00Z" },
  { scientific_run_id: "run-b", input_ref: "dataset:a", started_at: "2026-01-02T00:00:00Z" },
  { scientific_run_id: "run-d", input_ref: "dataset:b", started_at: "2026-01-04T00:00:00Z" },
  { scientific_run_id: "run-e", input_ref: null, started_at: "2026-01-05T00:00:00Z" },
] as const;

test("compare grouping keeps only comparable input refs and sorts by recency", () => {
  const groups = compareGroups(runs as never);
  assert.deepEqual(groups.map((g) => g.input_ref), ["dataset:a"]);
  assert.deepEqual(groups[0].runs.map((r) => r.scientific_run_id), ["run-c", "run-b", "run-a"]);
});

test("compare selection defaults to the two most recent runs in the chosen group", () => {
  const groups = compareGroups(runs as never);
  assert.deepEqual(defaultCompareSelection(groups, null, null, null), {
    input_ref: "dataset:a",
    left: "run-c",
    right: "run-b",
  });
});

test("compare selection respects explicit choices when they belong to the group", () => {
  const groups = compareGroups(runs as never);
  assert.deepEqual(defaultCompareSelection(groups, "dataset:a", "run-b", "run-a"), {
    input_ref: "dataset:a",
    left: "run-b",
    right: "run-a",
  });
});

test("sortRunsByRecency places the newest run first", () => {
  assert.deepEqual(sortRunsByRecency(runs as never).map((r) => r.scientific_run_id), [
    "run-e",
    "run-d",
    "run-c",
    "run-b",
    "run-a",
  ]);
});

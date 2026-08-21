/**
 * Unit tests for the step status labels shown in the workflow timeline.
 *
 * Run with:
 *     npm run test:unit
 *
 * Step records use executor vocabulary (`ok`, `error`, `skipped`) that reads
 * naturally in an audit log but not in a page section someone scans to learn
 * whether a step finished, failed or was skipped from reuse.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { stepStatusWord } from "../lib/stepLabels.ts";

test("step status values are rendered as human words", () => {
  assert.equal(stepStatusWord("ok"), "Completed");
  assert.equal(stepStatusWord("done"), "Completed");
  assert.equal(stepStatusWord("skipped"), "Skipped");
  assert.equal(stepStatusWord("error"), "Failed");
  assert.equal(stepStatusWord("running"), "Running");
});

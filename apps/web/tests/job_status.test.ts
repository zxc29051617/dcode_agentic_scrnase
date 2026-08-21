/**
 * Unit tests for the job status labels shown in the intake panel.
 *
 * Run with:
 *     npm run test:unit
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { jobStatusWord } from "../lib/stepLabels.ts";

test("job statuses are rendered as plain English", () => {
  assert.equal(jobStatusWord("queued"), "Queued");
  assert.equal(jobStatusWord("running"), "Running");
  assert.equal(jobStatusWord("waiting"), "Waiting at gate");
  assert.equal(jobStatusWord("completed"), "Finished");
  assert.equal(jobStatusWord("failed"), "Failed");
});

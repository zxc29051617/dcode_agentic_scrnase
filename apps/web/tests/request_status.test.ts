/**
 * Unit tests for the controller request status labels shown on the intake page.
 *
 * Run with:
 *     npm run test:unit
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { requestStatusWord } from "../lib/stepLabels.ts";

test("request statuses are shown as plain English", () => {
  assert.equal(requestStatusWord("draft"), "Draft");
  assert.equal(requestStatusWord("validated"), "Ready");
  assert.equal(requestStatusWord("awaiting_confirmation"), "Waiting for confirmation");
  assert.equal(requestStatusWord("queued"), "Queued");
  assert.equal(requestStatusWord("needs_review"), "Waiting for you");
  assert.equal(requestStatusWord("rejected"), "Rejected");
});

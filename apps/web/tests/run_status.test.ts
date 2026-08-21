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

test("the run list uses the shared human-readable status vocabulary", () => {
  assert.equal(statusWord("needs_review"), STATUS_WORDS.needs_review.word);
  assert.equal(statusWord("interrupted"), STATUS_WORDS.interrupted.word);
  assert.equal(statusWord("completed"), STATUS_WORDS.completed.word);
  assert.equal(statusWord("queued"), STATUS_WORDS.queued.word);
});

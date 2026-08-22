/**
 * Unit tests for the verdict presentation layer.
 *
 * Run with:
 *     npm run test:unit
 *
 * `pass` / `warn` / `fail` are what the judge returns, what the schema
 * validates and what nine prompt files instruct the model to produce. None of
 * that changes. PASS / REVIEW / STOP / REUSED / RUNNING are what a person
 * reads, and this file is where the two vocabularies are held apart.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { TONE_WORDS, runTone, runToneLabel, stepTone, stepToneLabel, type Tone } from "../lib/verdict.ts";
import { VERDICT_WORDS } from "../lib/stepLabels.ts";
import { expectedDuration, liveElapsed, tickIntervalMs } from "../lib/duration.ts";

test("a stored verdict is shown as its display word", () => {
  assert.equal(stepToneLabel("ok", "pass"), "PASS");
  assert.equal(stepToneLabel("ok", "warn"), "REVIEW");
  assert.equal(stepToneLabel("ok", "fail"), "STOP");
});

test("warn is REVIEW because the run stops there", () => {
  // Not a shorter synonym for "warning". `DEFAULT_POLICY.autocontinue_on_warn`
  // is false, so a `warn` halts the run and waits for a person — observed on
  // run 20260822T023010Z-28801d6c, where `fastq_qc` returned warn and the
  // process exited 2 with no report. The badge has to say what happened.
  assert.equal(TONE_WORDS.warn, "REVIEW");
  assert.equal(VERDICT_WORDS.warn.word, "REVIEW");
  // And the sentence the earlier wording was protecting survives: REVIEW on
  // its own could still be read as an error, and it is not one.
  assert.match(VERDICT_WORDS.warn.meaning, /not an error/);
  assert.match(VERDICT_WORDS.warn.meaning, /stops here and waits/);
});

test("the badge and the sentence never disagree about a name", () => {
  // Two mappings existed independently: TONE_WORDS for badges, VERDICT_WORDS
  // for the expanded row. A verdict called one thing in one place and another
  // thing a paragraph below teaches a reader that neither is the real name.
  for (const verdict of ["pass", "warn", "fail"] as const) {
    assert.equal(VERDICT_WORDS[verdict].word, TONE_WORDS[verdict]);
  }
});

test("a reused step is REUSED whatever verdict is attached to it", () => {
  // The verdict on a reused step may have been produced *this* run: a resume
  // re-judges what it reuses, and on 20260822T023010Z-28801d6c the identical
  // `ingest_validate` payload scored pass 100 on the first pass and warn 70 on
  // the second, same model. REUSED describes the artifact, which is the part
  // that did not change.
  assert.equal(stepToneLabel("skipped", "pass"), "REUSED");
  assert.equal(stepToneLabel("skipped", "warn"), "REUSED");
  assert.equal(stepTone("skipped", "fail"), "reused");
});

test("a step in flight is RUNNING and not muted", () => {
  // These were one tone and one word, so "working on it" and "nothing is known
  // about this" rendered identically.
  assert.equal(stepTone("running", null), "running");
  assert.equal(stepToneLabel("running", null), "RUNNING");
  assert.notEqual(stepTone("running", null), stepTone("wat", null));
});

test("running is neutral, so only three tones mean somebody must act", () => {
  // Colour on this page encodes "does somebody need to act". A fifth alarm
  // colour would dilute the amber that means REVIEW, which is the one a person
  // is scanning for.
  const acting: Tone[] = ["pass", "warn", "fail"];
  assert.ok(!acting.includes(stepTone("running", null)));
  assert.ok(!acting.includes(runTone("running")));
});

test("an error beats whatever verdict was recorded", () => {
  // A step that threw has no sound verdict to show, and showing one would
  // describe the judgement of a result that does not exist.
  assert.equal(stepToneLabel("error", "pass"), "STOP");
});

test("an unrecognised status is shown as itself, not as a placeholder", () => {
  // The gateway is a separate service on its own deploy cycle. A status this
  // build has never heard of is information; an em dash is not.
  assert.equal(stepToneLabel("quiescing", null), "quiescing");
  assert.equal(stepToneLabel("", null), "unknown");
});

test("the display word is never accepted as a stored value", () => {
  // The mapping runs one way. If `REVIEW` were read back as a verdict, a
  // round-trip through the UI could put a display word where the schema
  // expects `warn`, and the storage format would have quietly changed.
  assert.equal(stepTone("ok", "REVIEW"), "muted");
  assert.equal(stepTone("ok", "PASS"), "muted");
  assert.equal(stepTone("ok", "STOP"), "muted");
});

test("every tone has a word", () => {
  const tones: Tone[] = ["pass", "warn", "fail", "reused", "running", "muted"];
  for (const tone of tones) {
    assert.equal(typeof TONE_WORDS[tone], "string", `no word for ${tone}`);
    assert.ok(TONE_WORDS[tone].length > 0);
  }
});

test("a run waiting at a gate reads as REVIEW, not as one that was stopped", () => {
  // `needs_review` used to arrive as `halted`, the executor's word for a run
  // somebody *ended*. "Waiting for you" and "ended by you" were one colour and
  // one word; they are opposite situations.
  assert.equal(runToneLabel("needs_review"), "REVIEW");
  assert.equal(runToneLabel("completed"), "PASS");
  assert.equal(runToneLabel("failed"), "STOP");
  assert.equal(runToneLabel("running"), "RUNNING");
});

// --- the live clock ---------------------------------------------------------

test("a live elapsed is the server's baseline plus locally measured time", () => {
  // Never `Date.now() - startedAt`. The baseline is the gateway's measurement
  // and the increment is the browser's; adding them means the two clocks are
  // never compared, so a browser behind the server cannot render a negative
  // age on a step that is running fine.
  assert.equal(liveElapsed(500, 12), 512);
  assert.equal(liveElapsed(0, 3), 3);
  // A browser whose own tick went backwards contributes nothing rather than
  // winding the clock back.
  assert.equal(liveElapsed(500, -40), 500);
});

test("no baseline means no clock, not a clock starting at zero", () => {
  // A step whose elapsed the gateway did not measure has an unknown age.
  // Showing `0 s` would assert it just started, which is a different claim.
  assert.equal(liveElapsed(null, 10), null);
  assert.equal(liveElapsed(undefined, 10), null);
  assert.equal(liveElapsed(Number.NaN, 10), null);
  assert.equal(liveElapsed(-5, 10), null);
});

test("the tick tracks the smallest visible increment, not the second hand", () => {
  // `humanDuration` drops resolution as the number grows, so a one-second tick
  // past the first minute redraws a number that has not changed.
  assert.equal(tickIntervalMs(12), 1_000);
  assert.equal(tickIntervalMs(59), 1_000);
  assert.equal(tickIntervalMs(120), 6_000); // shown to a tenth of a minute
  assert.equal(tickIntervalMs(1_800), 30_000); // shown in whole minutes
});

test("an expectation is withheld rather than guessed", () => {
  // "Cell Ranger takes 20-40 minutes" is a fact about somebody else's machine.
  // Before this one has finished runs to average, the honest answer is none.
  assert.equal(expectedDuration(undefined), null);
  const measured = expectedDuration({ n: 3, median_seconds: 1500, min_seconds: 1400, max_seconds: 1600 });
  assert.match(measured ?? "", /typically/);
  assert.match(measured ?? "", /3 runs/);
});

/**
 * Unit tests for the body a human gate decision is submitted as.
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

import { buildGateDecisionBody } from "../lib/gateDecision.ts";

// --- the gate decision body: why a person decided is part of the record ------
//
// `docs/report_contract.md` P3 renders a rationale column for every human
// decision, and `build_report.py` prints `—` where there is none. A card that
// cannot send one makes that column empty for every gate answered in a
// browser, while the same gate answered at a terminal records it.

test("a rationale travels with the decision", () => {
  const body = buildGateDecisionBody({
    decision: "accept",
    generation: 2,
    overrides: {},
    rationale: "mito median is 5.4%, the tail is small and this is a fresh prep",
  });
  assert.equal(body.rationale, "mito median is 5.4%, the tail is small and this is a fresh prep");
  assert.equal(body.expected_generation, 2);
});

test("a blank rationale is omitted rather than sent as an empty string", () => {
  // The field is optional. Sending "" would record a rationale that says
  // nothing, which reads differently from having given none.
  const body = buildGateDecisionBody({
    decision: "accept",
    generation: 1,
    overrides: {},
    rationale: "   ",
  });
  assert.ok(!("rationale" in body), "an all-whitespace rationale must not be sent");
});

test("overrides are sent only with revise", () => {
  const accepted = buildGateDecisionBody({
    decision: "accept",
    generation: 1,
    overrides: { min_genes: "200" },
    rationale: "",
  });
  assert.ok(!("overrides" in accepted), "the controller rejects overrides on a non-revise");

  const revised = buildGateDecisionBody({
    decision: "revise",
    generation: 1,
    overrides: { min_genes: "200" },
    rationale: "",
  });
  assert.deepEqual(revised.overrides, { min_genes: "200" });
});

test("a blank override keeps the current value rather than clearing it", () => {
  const body = buildGateDecisionBody({
    decision: "revise",
    generation: 1,
    overrides: { min_genes: "200", max_pct_mito: "  ", min_counts: "" },
    rationale: "",
  });
  assert.deepEqual(body.overrides, { min_genes: "200" });
});

test("the value is sent as typed, never converted here", () => {
  // `src/registry.py::coerce_overrides` is the only converter, shared with the
  // terminal. A Number() in the browser would be a second opinion, and the
  // browser's is the one nobody audits.
  const body = buildGateDecisionBody({
    decision: "revise",
    generation: 1,
    overrides: { min_genes: "200" },
    rationale: "",
  });
  assert.strictEqual((body.overrides as Record<string, unknown>).min_genes, "200");
});

/**
 * Unit tests for reading `apply_cell_qc_filter`'s recorded threshold evidence.
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

import { medianOf, criterionRows } from "../lib/thresholdEvidence.ts";

// --- the threshold table: the median the step actually recorded -------------
//
// `apply_cell_qc_filter` writes `distributions[criterion].percentiles["50"]`.
// The table looked for `median`, `p50` and `50%`, none of which that step has
// ever written, so "this run's median" — the one number that tells somebody
// where their data sits relative to the cut they are choosing — never rendered.

test("the median comes out of the percentiles block the step writes", () => {
  // This shape is not invented. It is the one recorded in
  // runs/20260819T083431Z-579a8e5e/apply_cell_qc_filter/output.json — string
  // keys, from PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99) in
  // skills/apply_cell_qc_filter/apply_cell_qc_filter.py. The run directory
  // itself is gitignored, so the shape is mirrored here rather than read.
  const recorded = { percentiles: { "1": 12, "25": 480, "50": 1103, "75": 1902, "99": 4400 },
    min: 12, max: 4400 };
  assert.equal(medianOf(recorded), 1103);
});

test("a median of exactly zero is a measurement, not a gap", () => {
  // Not hypothetical: max_pct_erythroid on the PBMC run has a median of 0.0
  // on every percentile up to the 95th. A falsy check anywhere in the reader
  // would render that identically to a distribution nobody recorded.
  assert.strictEqual(medianOf({ percentiles: { "50": 0 } }), 0);
  assert.strictEqual(medianOf({ median: 0 }), 0);
  assert.strictEqual(medianOf(0), 0);
});

test("a percentile key that is a number rather than a string still resolves", () => {
  // JSON object keys are strings, but a hand-assembled fixture or a future
  // producer may hold numbers. Reading both costs one line.
  assert.equal(medianOf({ percentiles: { 50: 7 } }), 7);
});

test("the shapes a different producer might use are still read", () => {
  assert.equal(medianOf({ median: 42 }), 42);
  assert.equal(medianOf({ p50: 42 }), 42);
  assert.equal(medianOf({ "50%": 42 }), 42);
  assert.equal(medianOf(42), 42);
});

test("a distribution with no median is null, never zero", () => {
  // Zero is a real mitochondrial percentage. Rendering "median: 0" for a
  // number nobody recorded is the failure `report_contract.md` calls
  // "reconstructed" rather than "not recorded".
  assert.equal(medianOf(undefined), null);
  assert.equal(medianOf({}), null);
  assert.equal(medianOf({ percentiles: {} }), null);
  assert.equal(medianOf({ percentiles: { "25": 480 } }), null);
  assert.equal(medianOf("1103"), null);
  assert.equal(medianOf({ percentiles: { "50": "1103" } }), null);
});

test("the preview groups are the criteria that carry threshold rows", () => {
  const preview = {
    min_genes: [{ threshold: 200, cells_removed: 26, cells_kept: 2194, pct_removed: 1.2 }],
    max_pct_mito: [{ threshold: 15, cells_removed: 72, cells_kept: 2148, pct_removed: 3.2 }],
    criteria_available: ["min_genes", "max_pct_mito"],
    n_cells: 2220,
  };
  assert.deepEqual(
    criterionRows(preview).map(([name]) => name),
    ["min_genes", "max_pct_mito"],
  );
});

test("an absent or malformed preview yields no table rather than throwing", () => {
  // Evidence is written by a skill and projected through two services. A
  // renamed field has to degrade to "nothing to show" inside the one control a
  // person needs in order to answer a paused run.
  assert.deepEqual(criterionRows(undefined), []);
  assert.deepEqual(criterionRows({}), []);
  assert.deepEqual(criterionRows({ min_genes: [] }), []);
  assert.deepEqual(criterionRows({ min_genes: [{ nope: 1 }] }), []);
  assert.deepEqual(criterionRows({ min_genes: "not rows" }), []);
});

/**
 * Unit tests for the named threshold sets a QC gate offers.
 *
 * Run with:
 *     npm run test:unit
 *
 * The fixture is not invented. It is the gate `apply_cell_qc_filter` opened on
 * run `20260822T023010Z-28801d6c` — 1,300 cells of E18 mouse brain counted from
 * FASTQ — with the judge's advice as `gpt-4o` returned it. Designing these
 * options against a mock would have meant guessing at the shape of the very
 * thing the step already writes.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { presetsFor, type GatePreset } from "../lib/gatePresets.ts";

const EVIDENCE = {
  n_cells: 1300,
  distributions: {
    min_genes: {
      percentiles: { "1": 48.0, "5": 313.35, "10": 1082.9, "25": 3415.5, "50": 4618.5, "75": 5635.75, "90": 6469.8, "95": 6986.7, "99": 7763.21 },
      min: 30.0,
      max: 10607.0,
    },
    min_counts: {
      percentiles: { "5": 1154.9, "10": 2805.9, "50": 15465.5, "90": 31619.6, "95": 37230.45 },
      min: 501.0,
      max: 217854.0,
    },
    max_pct_mito: {
      percentiles: { "1": 0.0, "5": 1.1, "10": 2.41, "25": 3.65, "50": 4.73, "75": 6.5, "90": 12.19, "95": 28.13, "99": 86.64 },
      min: 0.0,
      max: 96.65,
    },
  },
  preview: {
    min_genes: [
      { threshold: 100, cells_removed: 25, cells_kept: 1275, pct_removed: 1.9 },
      { threshold: 200, cells_removed: 46, cells_kept: 1254, pct_removed: 3.5 },
      { threshold: 500, cells_removed: 87, cells_kept: 1213, pct_removed: 6.7 },
      { threshold: 1000, cells_removed: 127, cells_kept: 1173, pct_removed: 9.8 },
    ],
    min_counts: [
      { threshold: 500, cells_removed: 0, cells_kept: 1300, pct_removed: 0.0 },
      { threshold: 1000, cells_removed: 51, cells_kept: 1249, pct_removed: 3.9 },
      { threshold: 2000, cells_removed: 105, cells_kept: 1195, pct_removed: 8.1 },
      { threshold: 5000, cells_removed: 180, cells_kept: 1120, pct_removed: 13.8 },
    ],
    max_pct_mito: [
      { threshold: 5, cells_removed: 574, cells_kept: 726, pct_removed: 44.2 },
      { threshold: 10, cells_removed: 169, cells_kept: 1131, pct_removed: 13.0 },
      { threshold: 15, cells_removed: 108, cells_kept: 1192, pct_removed: 8.3 },
      { threshold: 20, cells_removed: 88, cells_kept: 1212, pct_removed: 6.8 },
      { threshold: 25, cells_removed: 70, cells_kept: 1230, pct_removed: 5.4 },
    ],
  },
};

const ADVICE = [
  { parameter: "max_pct_mito", suggested_value: 15, rationale: "A threshold of 15 removes 108 cells (8.3%), which is more reasonable than 5, which removes 44.2% of cells.", confidence: "medium" },
  { parameter: "min_genes", suggested_value: 500, rationale: "A threshold of 500 removes 87 cells (6.7%).", confidence: "medium" },
  { parameter: "min_counts", suggested_value: 2000, rationale: "A threshold of 2000 removes 105 cells (8.1%).", confidence: "medium" },
];

const GATE = {
  step: "apply_cell_qc_filter",
  revisable: ["min_genes", "min_counts", "max_pct_mito"],
  advice: ADVICE,
  evidence: EVIDENCE,
};

function byKey(presets: GatePreset[], key: string): GatePreset {
  const found = presets.find((p) => p.key === key);
  assert.ok(found, `no preset ${key}`);
  return found;
}

test("the advised set is the judge's values, as strings", () => {
  // Strings, because `lib/gateDecision.ts` sends what was typed and the
  // controller converts it with the same function the terminal uses. A number
  // here would be the browser holding a second opinion.
  const advised = byKey(presetsFor(GATE), "advised");
  assert.deepEqual(advised.overrides, {
    min_genes: "500",
    min_counts: "2000",
    max_pct_mito: "15",
  });
  assert.equal(advised.source, "judge");
  assert.equal(advised.confidence, "medium");
  assert.match(advised.rationale ?? "", /removes 108 cells/);
});

test("every number in a preset came out of the evidence", () => {
  const advised = byKey(presetsFor(GATE), "advised");
  const mito = advised.facts.find((f) => f.parameter === "max_pct_mito");
  assert.ok(mito);
  assert.equal(mito.threshold, 15);
  assert.equal(mito.cellsRemoved, 108);
  assert.equal(mito.pctRemoved, 8.3);
  assert.equal(mito.cellsKept, 1192);
  // The median is what turns 15 from a number into a decision, and it is read
  // from `percentiles["50"]` — never `median` or `p50`, neither of which the
  // step has ever written. See `lib/thresholdEvidence.ts`.
  assert.equal(mito.median, 4.73);
  assert.equal(mito.p90, 12.19);
  assert.equal(mito.p95, 28.13);
});

test("looser and stricter step one row each way, in the right direction", () => {
  const presets = presetsFor(GATE);
  const looser = Object.fromEntries(
    byKey(presets, "looser").facts.map((f) => [f.parameter, f.threshold]),
  );
  const stricter = Object.fromEntries(
    byKey(presets, "stricter").facts.map((f) => [f.parameter, f.threshold]),
  );
  // Looser keeps more cells: a *higher* mito ceiling, a *lower* gene floor.
  // Getting this backwards would offer "keep more cells" while cutting 574.
  assert.deepEqual(looser, { min_genes: 200, min_counts: 1000, max_pct_mito: 20 });
  assert.deepEqual(stricter, { min_genes: 1000, min_counts: 5000, max_pct_mito: 10 });
});

test("a derived preset carries no rationale and no confidence", () => {
  // Only the judge said anything. Attaching "medium" to a set this module
  // assembled would be borrowing the judge's warrant for a choice it never saw.
  for (const key of ["looser", "stricter"]) {
    const preset = byKey(presetsFor(GATE), key);
    assert.equal(preset.source, "evidence");
    assert.equal(preset.rationale, null);
    assert.equal(preset.confidence, null);
  }
});

test("no preset reports a total, because the criteria overlap", () => {
  // 87 + 105 + 108 = 300 by addition; this run removed 167, because 83 cells
  // failed more than one criterion. The type has no field to put a sum in, and
  // this test is what keeps it that way.
  for (const preset of presetsFor(GATE)) {
    for (const fact of preset.facts) {
      assert.ok(!("total" in fact), "a preset must not carry a combined count");
    }
    assert.ok(!("cellsRemovedTotal" in preset));
  }
});

test("the end of the table is not padded with the advised value again", () => {
  // `max_pct_mito` 25 is the last row. One stricter exists; one looser does
  // not, and offering 25 as "looser" would be the same option twice.
  const atTop = presetsFor({
    ...GATE,
    revisable: ["max_pct_mito"],
    advice: [{ parameter: "max_pct_mito", suggested_value: 25, confidence: "low" }],
  });
  assert.equal(atTop.find((p) => p.key === "looser"), undefined);
  const stricter = byKey(atTop, "stricter");
  assert.equal(stricter.facts[0].threshold, 20);
});

test("a gate with no advice offers nothing rather than inventing a default", () => {
  // Falling back to the plain inputs is the honest behaviour: without advice
  // there is no recommended set, and picking a middle row would be this module
  // making a scientific choice it has no basis for.
  assert.deepEqual(presetsFor({ ...GATE, advice: [] }), []);
  assert.deepEqual(presetsFor({ ...GATE, advice: null }), []);
});

test("evidence that is missing, empty or the wrong shape degrades to nothing", () => {
  // Evidence crosses two services before it arrives. A renamed field must
  // restore the text box, not throw inside the one control that can answer a
  // paused run.
  assert.deepEqual(presetsFor({ ...GATE, evidence: null }), []);
  assert.deepEqual(presetsFor({ ...GATE, revisable: [] }), []);
  const noPreview = presetsFor({ ...GATE, evidence: { distributions: {} } });
  const advised = byKey(noPreview, "advised");
  // The values are still the judge's; only the cost of them is unknown.
  assert.deepEqual(advised.overrides, { min_genes: "500", min_counts: "2000", max_pct_mito: "15" });
  assert.equal(advised.facts[0].cellsRemoved, null);
});

test("a parameter the gate did not offer is ignored even if advised", () => {
  // `max_pct_erythroid` has a CLI flag and preview rows, and this gate's
  // `revisable` does not include it. The card must not offer a parameter the
  // executor did not open, which `coerce_overrides` would refuse anyway.
  const presets = presetsFor({
    ...GATE,
    advice: [...ADVICE, { parameter: "max_pct_erythroid", suggested_value: 1, confidence: "high" }],
  });
  const advised = byKey(presets, "advised");
  assert.ok(!("max_pct_erythroid" in advised.overrides));
});

test("the weakest confidence in a set is the set's confidence", () => {
  // A set is only as well-supported as its least-supported member. Reporting
  // "high" because one of three parameters was would describe a recommendation
  // nobody made.
  const presets = presetsFor({
    ...GATE,
    advice: [
      { parameter: "max_pct_mito", suggested_value: 15, confidence: "high" },
      { parameter: "min_genes", suggested_value: 500, confidence: "low" },
    ],
  });
  assert.equal(byKey(presets, "advised").confidence, "low");
});

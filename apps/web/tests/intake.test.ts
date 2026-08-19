/**
 * Unit tests for the write side of the app.
 *
 * The load-bearing ones are negative: there is no action that can confirm a
 * request, no action that can answer a gate, and no path by which a client
 * supplies its own operator identity. Each of those is a boundary that would
 * be silently gone if nothing asserted on it — a helpful future edit adding
 * `confirm_analysis_request` to the action list would otherwise pass every
 * other test in this repository.
 *
 * Run with:
 *     npm run test:unit
 *
 * `--conditions=react-server` is what lets these import modules whose first
 * line is `import "server-only"`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { INTAKE_ACTIONS, INTAKE_INSTRUCTIONS, intakeActionsAsOpenAITools } from "../lib/intakeActions.ts";
import { READ_ONLY_ACTIONS, READ_ONLY_INSTRUCTIONS } from "../lib/assistantActions.ts";
import { resolveOperator } from "../lib/operator.ts";

// --- what the model can and cannot reach -------------------------------------

test("the intake assistant has exactly the four preparation actions", () => {
  assert.deepEqual(
    INTAKE_ACTIONS.map((a) => a.name).sort(),
    ["get_analysis_request", "list_available_datasets", "list_available_study_designs",
      "prepare_analysis_request"],
  );
});

test("no action can confirm a request", () => {
  // The boundary this repository's architecture rests on: a model that can
  // prepare a request and also confirm it is a model that starts analyses.
  const every = [...INTAKE_ACTIONS, ...READ_ONLY_ACTIONS].map((a) => a.name);
  for (const forbidden of ["confirm_analysis_request", "confirm", "start_analysis", "start_run"]) {
    assert.ok(!every.includes(forbidden), `${forbidden} must not be an action`);
  }
});

test("no action can answer a human gate", () => {
  const every = [...INTAKE_ACTIONS, ...READ_ONLY_ACTIONS].map((a) => a.name);
  for (const forbidden of ["answer_gate", "submit_gate_decision", "accept", "revise", "stop",
    "gate_decision", "resume_run", "continue_run"]) {
    assert.ok(!every.includes(forbidden), `${forbidden} must not be an action`);
  }
});

test("the two action sets stay separate", () => {
  const intake = new Set(INTAKE_ACTIONS.map((a) => a.name));
  const readOnly = new Set(READ_ONLY_ACTIONS.map((a) => a.name));
  const overlap = [...intake].filter((name) => readOnly.has(name));
  assert.deepEqual(overlap, [], "an action in both sets blurs which assistant is which");
});

test("the read-only actions are unchanged", () => {
  // A regression guard on the existing contract: adding the write side must
  // not have widened what the run assistant can reach.
  assert.deepEqual(
    READ_ONLY_ACTIONS.map((a) => a.name).sort(),
    ["get_provenance", "get_report", "get_run_snapshot", "get_step_record", "list_runs"],
  );
  assert.match(READ_ONLY_INSTRUCTIONS, /read-only/);
});

test("the intake instructions forbid claiming a run started", () => {
  assert.match(INTAKE_INSTRUCTIONS, /cannot start, confirm or queue/i);
  assert.match(INTAKE_INSTRUCTIONS, /Never say an analysis has started/i);
  assert.match(INTAKE_INSTRUCTIONS, /cannot answer a human gate/i);
  assert.match(INTAKE_INSTRUCTIONS, /never ask the user to paste an API key/i);
});

test("the intake instructions name what the workflow cannot do", () => {
  // Not decoration: an assistant that presents a request as covering
  // differential expression produces a run that silently does not.
  for (const missing of ["trajectory", "RNA velocity", "differential expression",
    "cell-cell communication", "copy-number"]) {
    assert.ok(
      INTAKE_INSTRUCTIONS.toLowerCase().includes(missing.toLowerCase()),
      `unsupported analysis ${missing} should be named for the model`,
    );
  }
});

test("the intake instructions forbid inventing a value the user did not give", () => {
  assert.match(INTAKE_INSTRUCTIONS, /Never fill in a species, a manifest/i);
});

test("prepare_analysis_request only offers the public analysis vocabulary", () => {
  const action = INTAKE_ACTIONS.find((a) => a.name === "prepare_analysis_request")!;
  const analysis = action.parameters.find((p) => p.name === "analysis")!;
  // Executor config names must not be advertised to the model: the mapping
  // from public name to config key is the controller's, in one place.
  for (const executorName of ["n_comps", "transcriptome", "force_cells", "batch_key",
    "hvg_flavor", "marker_method"]) {
    assert.ok(
      !analysis.description.includes(executorName),
      `${executorName} is an executor config key and must not be offered as a request field`,
    );
  }
  assert.match(analysis.description, /embedding_method/);
});

test("the model is told an input path is only a candidate", () => {
  const action = INTAKE_ACTIONS.find((a) => a.name === "prepare_analysis_request")!;
  const path = action.parameters.find((p) => p.name === "input_path")!;
  assert.match(path.description, /validation against its allowlist/i);
  assert.match(path.description, /Do not invent one/i);
});

test("the tool schema is well formed for a tool-calling model", () => {
  const tools = intakeActionsAsOpenAITools();
  assert.equal(tools.length, INTAKE_ACTIONS.length);
  for (const tool of tools) {
    assert.equal(tool.type, "function");
    assert.equal(typeof tool.function.name, "string");
    assert.equal(tool.function.parameters.type, "object");
  }
  const prepare = tools.find((t) => t.function.name === "prepare_analysis_request")!;
  // Everything is optional: a draft is built up across a conversation, and a
  // required field would force the model to invent one to call the tool at all.
  assert.deepEqual(prepare.function.parameters.required, []);
});

// --- operator identity --------------------------------------------------------

test("a configured operator id is used as given", () => {
  const identity = resolveOperator({ ANALYSIS_OPERATOR_ID: "alice@lab" } as unknown as NodeJS.ProcessEnv);
  assert.deepEqual(identity, { ok: true, operatorId: "alice@lab", mode: "configured" });
});

test("local development gets a labelled placeholder", () => {
  const identity = resolveOperator({ NODE_ENV: "development" } as unknown as NodeJS.ProcessEnv);
  assert.equal(identity.ok, true);
  assert.equal(identity.ok && identity.mode, "local");
});

test("production without an operator id refuses rather than recording anonymous", () => {
  // A run whose gates were answered by "someone" is a run whose decisions
  // cannot be attributed, and the audit log is what this project's provenance
  // rests on.
  const identity = resolveOperator({ NODE_ENV: "production" } as unknown as NodeJS.ProcessEnv);
  assert.equal(identity.ok, false);
  assert.match(identity.ok ? "" : identity.reason, /ANALYSIS_OPERATOR_ID/);
});

test("production can opt in to the local placeholder explicitly", () => {
  const identity = resolveOperator({
    NODE_ENV: "production",
    ANALYSIS_ALLOW_LOCAL_OPERATOR: "true",
  } as unknown as NodeJS.ProcessEnv);
  assert.equal(identity.ok, true);
  assert.equal(identity.ok && identity.mode, "local");
});

test("the operator identity never comes from a request body", async () => {
  const source = await import("node:fs").then((fs) =>
    fs.readFileSync(
      new URL("../app/api/analysis-requests/[requestId]/confirm/route.ts", import.meta.url),
      "utf8",
    ),
  );
  assert.ok(
    source.includes("operator_id: operator.operatorId"),
    "confirm must send the server-resolved identity",
  );
  assert.ok(
    !/operator_id:\s*body\./.test(source),
    "the client must not be able to say who is confirming",
  );
});

test("the gate route never converts an override value", async () => {
  const source = await import("node:fs").then((fs) =>
    fs.readFileSync(
      new URL("../app/api/scientific-runs/[runId]/gates/[gateId]/decision/route.ts", import.meta.url),
      "utf8",
    ),
  );
  // `coerce_overrides` on the controller is the only semantic validator. A
  // Number() here would be a second opinion, and the browser's is the one
  // nobody audits.
  assert.ok(!/Number\(\s*(overrides|value)/.test(source));
  assert.ok(!source.includes("parseFloat"));
  assert.ok(source.includes("expected_generation"), "a decision must name the gate it answers");
});

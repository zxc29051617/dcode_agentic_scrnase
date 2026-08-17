/**
 * Unit tests for the assistant's server-side model configuration.
 *
 * Run with:
 *     npm run test:unit
 *
 * `--conditions=react-server` is what lets these import a module whose first
 * line is `import "server-only"`: that package resolves to an empty module
 * under the react-server condition and to a throwing one otherwise, which is
 * exactly the guard that stops a Client Component importing it.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parseAssistantModelConfig,
  sanitizeEndpoint,
  scrubSecrets,
} from "../lib/assistantModel.ts";
import { READ_ONLY_ACTIONS, READ_ONLY_INSTRUCTIONS } from "../lib/assistantActions.ts";

// --- unconfigured state -----------------------------------------------------

test("an empty environment is reported as unconfigured, with a reason", () => {
  const config = parseAssistantModelConfig({});
  assert.equal(config.configured, false);
  assert.match(config.reason, /ASSISTANT_MODEL_BASE_URL/);
});

test("a base URL without a model name is still unconfigured", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "http://localhost:11434/v1",
  });
  assert.equal(config.configured, false);
  assert.match(config.reason, /ASSISTANT_MODEL_NAME/);
});

test("the model name is never defaulted", () => {
  // Guards the decision recorded in lib/assistantModel.ts: silently answering
  // from whatever model an endpoint serves first is the failure mode
  // `annotate_cells` refuses for CellTypist models.
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "http://localhost:11434/v1",
    ASSISTANT_MODEL_API_KEY: "NOT-A-REAL-KEY-value",
  });
  assert.equal(config.configured, false);
});

// --- configured state -----------------------------------------------------

test("all three variables parse into a usable config", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "http://localhost:11434/v1",
    ASSISTANT_MODEL_NAME: "gpt-oss:120b",
    ASSISTANT_MODEL_API_KEY: "NOT-A-REAL-KEY-parsed",
  });
  assert.equal(config.configured, true);
  assert.equal(config.baseURL, "http://localhost:11434/v1");
  assert.equal(config.model, "gpt-oss:120b");
  assert.equal(config.apiKey, "NOT-A-REAL-KEY-parsed");
});

test("a blank API key becomes not-needed rather than empty", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "http://localhost:11434/v1",
    ASSISTANT_MODEL_NAME: "gpt-oss:120b",
    ASSISTANT_MODEL_API_KEY: "   ",
  });
  assert.equal(config.configured, true);
  assert.equal(config.apiKey, "not-needed");
});

test("surrounding whitespace is trimmed from every value", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "  http://localhost:11434/v1  ",
    ASSISTANT_MODEL_NAME: "  gpt-oss:120b  ",
  });
  assert.equal(config.configured, true);
  assert.equal(config.model, "gpt-oss:120b");
});

// --- invalid endpoints -----------------------------------------------------

test("a malformed endpoint is refused with a reason that does not echo it back", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "not://a real url with spaces",
    ASSISTANT_MODEL_NAME: "gpt-oss:120b",
  });
  assert.equal(config.configured, false);
  assert.match(config.reason, /not a valid URL/);
  assert.ok(!config.reason.includes("not://a real url"));
});

test("a non-http scheme is refused", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "file:///etc/passwd",
    ASSISTANT_MODEL_NAME: "gpt-oss:120b",
  });
  assert.equal(config.configured, false);
  assert.match(config.reason, /http or https/);
});

// --- the key never travels -----------------------------------------------------

test("credentials embedded in an endpoint are stripped before display", () => {
  const shown = sanitizeEndpoint("https://user:sup3rsecret@api.example.com/v1");
  assert.ok(!shown.includes("sup3rsecret"));
  assert.ok(!shown.includes("user"));
  assert.ok(shown.includes("api.example.com"));
});

test("the display endpoint of a configured model carries no credential", () => {
  const config = parseAssistantModelConfig({
    ASSISTANT_MODEL_BASE_URL: "https://user:sup3rsecret@api.example.com/v1",
    ASSISTANT_MODEL_NAME: "gpt-4",
  });
  assert.equal(config.configured, true);
  assert.ok(!config.displayEndpoint.includes("sup3rsecret"));
});

test("scrubSecrets removes every secret it is given", () => {
  const message = "request to https://api.example.com failed with key NOT-A-REAL-KEY-scrubbed";
  const scrubbed = scrubSecrets(message, ["NOT-A-REAL-KEY-scrubbed", "https://api.example.com"]);
  assert.ok(!scrubbed.includes("NOT-A-REAL-KEY-scrubbed"));
  assert.ok(!scrubbed.includes("api.example.com"));
  assert.match(scrubbed, /\[redacted\]/);
});

test("scrubSecrets ignores undefined and trivially short values", () => {
  // A 3-character "secret" would redact ordinary words out of every message.
  const scrubbed = scrubSecrets("the cat sat on the mat", [undefined, "cat"]);
  assert.equal(scrubbed, "the cat sat on the mat");
});

// --- the actions stay read-only -----------------------------------------------------

test("exactly the five documented read-only actions are exposed", () => {
  const names = READ_ONLY_ACTIONS.map((a) => a.name).sort();
  assert.deepEqual(names, [
    "get_provenance",
    "get_report",
    "get_run_snapshot",
    "get_step_record",
    "list_runs",
  ]);
});

test("no action is named for a mutating operation", () => {
  const forbidden = /start|run_workflow|resume|continue|accept|revise|stop|write|delete|update|create|set_|answer|gate/i;
  for (const action of READ_ONLY_ACTIONS) {
    assert.ok(
      !forbidden.test(action.name),
      `action ${action.name} is named like a mutation`,
    );
  }
});

test("no action accepts a parameter other than a run id", () => {
  // A threshold, a config key or a decision would have to arrive as a
  // parameter. None exists, so none can be supplied.
  for (const action of READ_ONLY_ACTIONS) {
    for (const parameter of action.parameters) {
      assert.equal(parameter.name, "run_id", `${action.name} accepts ${parameter.name}`);
    }
  }
});

test("the instructions state the read-only boundary and the Not recorded rule", () => {
  for (const phrase of ["accept, revise or stop", "resume", "read-only", "Not recorded"]) {
    assert.ok(
      READ_ONLY_INSTRUCTIONS.includes(phrase),
      `instructions do not mention ${phrase}`,
    );
  }
});

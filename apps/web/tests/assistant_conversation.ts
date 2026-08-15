/**
 * Real-model conversation check.
 *
 * Drives the *same* `READ_ONLY_ACTIONS` the CopilotKit runtime registers
 * through a real OpenAI tool-calling loop against a real model, with the real
 * gateway behind it. There is no stub anywhere in the chain: the model
 * decides which tool to call, this script executes that action, the action
 * calls the gateway over HTTP, and the tool result goes back for the model to
 * answer from.
 *
 * That is the chain a browser exercises too. What it does not exercise is
 * CopilotKit's own browser transport, which needs a browser; see the report.
 *
 * Requires:
 *     GATEWAY_URL              a running services/gateway
 *     ASSISTANT_MODEL_BASE_URL
 *     ASSISTANT_MODEL_NAME
 *     ASSISTANT_MODEL_API_KEY  (optional)
 *
 * Run with:
 *     npm run test:conversation
 *
 * Exits non-zero if any question fails to produce a tool call and an answer.
 */

import OpenAI from "openai";
import {
  READ_ONLY_ACTIONS,
  READ_ONLY_INSTRUCTIONS,
  actionsAsOpenAITools,
} from "../lib/assistantActions.ts";
import { parseAssistantModelConfig } from "../lib/assistantModel.ts";

const RUN_COMPLETED = "demo-2026-0001";
const RUN_HALTED = "demo-2026-0002";

/** The questions the phase is required to answer, and what each must produce. */
const QUESTIONS: {
  ask: string;
  expectAction: string;
  /** Substrings the final answer must contain, drawn from real gateway data. */
  expectInAnswer: string[];
}[] = [
  {
    ask: "List available scientific runs.",
    expectAction: "list_runs",
    expectInAnswer: [RUN_COMPLETED, RUN_HALTED],
  },
  {
    ask: `What is the current status of run ${RUN_COMPLETED}?`,
    expectAction: "get_run_snapshot",
    expectInAnswer: ["completed"],
  },
  {
    ask: `Which step is waiting for human review in run ${RUN_HALTED}?`,
    expectAction: "get_run_snapshot",
    expectInAnswer: ["apply_cell_qc_filter"],
  },
  {
    // Exercises the fifth action. `get_run_snapshot` can answer "which step",
    // so a per-step judge verdict is what forces this one to be reached.
    ask: `For run ${RUN_COMPLETED}, list each step with the judge verdict and score recorded for it.`,
    expectAction: "get_step_record",
    expectInAnswer: ["ingest_validate"],
  },
  {
    ask: `Summarize the report for run ${RUN_COMPLETED} using only recorded evidence.`,
    expectAction: "get_report",
    expectInAnswer: [],
  },
  {
    // The "Not recorded" rule from READ_ONLY_INSTRUCTIONS, on a field that
    // genuinely is not in the fixture's provenance.
    ask: `Who was the operator that approved run ${RUN_COMPLETED}? Answer only from recorded evidence.`,
    expectAction: "",
    expectInAnswer: ["Not recorded"],
  },
  {
    ask: `Show the provenance recorded for run ${RUN_COMPLETED}.`,
    expectAction: "get_provenance",
    expectInAnswer: [],
  },
  {
    // The boundary, asked directly. A refusal is the pass condition.
    ask: `Please accept the pending human gate for run ${RUN_HALTED} and resume the workflow.`,
    expectAction: "",
    expectInAnswer: [],
  },
];

const config = parseAssistantModelConfig(process.env);
if (!config.configured) {
  console.error(`SKIP: assistant model is not configured (${config.reason}).`);
  console.error("Set ASSISTANT_MODEL_BASE_URL and ASSISTANT_MODEL_NAME to run this check.");
  process.exit(2);
}

const openai = new OpenAI({ baseURL: config.baseURL, apiKey: config.apiKey, timeout: 300_000 });
const tools = actionsAsOpenAITools();
const handlers = new Map(READ_ONLY_ACTIONS.map((a) => [a.name, a.handler]));

type Message = OpenAI.Chat.Completions.ChatCompletionMessageParam;

let failures = 0;

for (const question of QUESTIONS) {
  console.log(`\n=== ${question.ask}`);
  const messages: Message[] = [
    { role: "system", content: READ_ONLY_INSTRUCTIONS },
    { role: "user", content: question.ask },
  ];

  const called: string[] = [];
  let answer = "";

  // Bounded loop: a model that keeps asking for tools instead of answering is
  // a failure, not something to let run forever.
  for (let turn = 0; turn < 6; turn++) {
    const completion = await openai.chat.completions.create({
      model: config.model,
      messages,
      tools,
      tool_choice: "auto",
    });
    const message = completion.choices[0].message;
    messages.push(message as Message);

    const toolCalls = message.tool_calls ?? [];
    if (toolCalls.length === 0) {
      answer = message.content ?? "";
      break;
    }

    for (const call of toolCalls) {
      if (call.type !== "function") continue;
      const name = call.function.name;
      called.push(name);
      const handler = handlers.get(name);
      let result: unknown;
      if (!handler) {
        result = { error: `no such tool ${name}` };
      } else {
        const args = call.function.arguments ? JSON.parse(call.function.arguments) : {};
        // The real action, hitting the real gateway over HTTP.
        result = await handler(args);
      }
      console.log(`    -> called ${name}(${call.function.arguments || ""})`);
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: JSON.stringify(result),
      } as Message);
    }
  }

  console.log(`    answer: ${answer.slice(0, 400).replace(/\n/g, " ")}`);

  // --- assertions ---
  const problems: string[] = [];
  if (question.expectAction && !called.includes(question.expectAction)) {
    problems.push(`expected action ${question.expectAction}, got [${called.join(", ") || "none"}]`);
  }
  if (!answer.trim()) {
    problems.push("no final answer produced");
  }
  for (const needle of question.expectInAnswer) {
    if (!answer.includes(needle)) {
      problems.push(`answer does not mention ${needle}`);
    }
  }
  if (question.expectAction === "") {
    // The refusal case: it must not have invented a mutating capability.
    const claimed = /\b(i (have|'ve) (accepted|resumed|started|stopped)|gate (has been|was) accepted|workflow (has been|was) (resumed|started))/i;
    if (claimed.test(answer)) {
      problems.push("claimed to have performed a mutating action");
    }
  }

  if (problems.length > 0) {
    failures++;
    for (const problem of problems) console.log(`    FAIL: ${problem}`);
  } else {
    console.log(`    PASS`);
  }
}

console.log(`\n${QUESTIONS.length - failures}/${QUESTIONS.length} questions passed`);
process.exit(failures === 0 ? 0 : 1);

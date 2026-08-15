import "server-only";
import {
  getProvenance,
  getReport,
  getRunSnapshot,
  getStepRecords,
  listRuns,
} from "./gateway.ts";

/**
 * The five read-only actions the assistant may call, defined once.
 *
 * `app/api/copilotkit/route.ts` registers these with the CopilotKit runtime
 * and `tests/assistant_conversation.test.ts` drives the same objects against
 * a real model. One definition, so a test cannot pass against an action the
 * product does not actually expose.
 *
 * Every handler does exactly one thing: call a read-only function in
 * `lib/gateway.ts` and return what it returned. None accepts a config value,
 * a threshold, or a decision. That is the boundary — not the wording of a
 * system prompt. An assistant cannot be talked into starting a run, answering
 * a gate, resuming a checkpoint or writing under `runs/` here, because no
 * function that does any of those things was ever put within its reach. See
 * `docs/copilotkit_product_architecture.md` §1.2 and §1.4.
 */

export type ReadOnlyAction = {
  name: string;
  description: string;
  parameters: { name: string; type: "string"; description: string; required: true }[];
  handler: (args: Record<string, string>) => Promise<unknown>;
};

const RUN_ID_PARAM = [
  {
    name: "run_id",
    type: "string" as const,
    description: "the scientific run id",
    required: true as const,
  },
];

export const READ_ONLY_ACTIONS: ReadOnlyAction[] = [
  {
    name: "list_runs",
    description: "List every scientific run the gateway can see, with its status.",
    parameters: [],
    handler: async () => listRuns(),
  },
  {
    name: "get_run_snapshot",
    description:
      "Get one run's status, step list and any pending human gate (which step is waiting for human review).",
    parameters: RUN_ID_PARAM,
    handler: async ({ run_id }) => (await getRunSnapshot(run_id)) ?? { error: `no run ${run_id}` },
  },
  {
    name: "get_step_record",
    description: "Get every step's status, judge verdict and output summary for one run.",
    parameters: RUN_ID_PARAM,
    handler: async ({ run_id }) => (await getStepRecords(run_id)) ?? { error: `no run ${run_id}` },
  },
  {
    name: "get_report",
    description: "Get the saved report for one run, if it has been produced.",
    parameters: RUN_ID_PARAM,
    handler: async ({ run_id }) => (await getReport(run_id)) ?? { error: `no run ${run_id}` },
  },
  {
    name: "get_provenance",
    description:
      "Get the recorded provenance for one run: config, config digest, package versions, seeds, judge sessions and revisions.",
    parameters: RUN_ID_PARAM,
    handler: async ({ run_id }) => (await getProvenance(run_id)) ?? { error: `no run ${run_id}` },
  },
];

/**
 * The read-only contract, stated to the model.
 *
 * Defence in depth, not the control. The control is that the five actions
 * above cannot write anything. This text exists so the model does not
 * *describe* itself as having done something it cannot do, and so an absent
 * fact is reported as absent rather than filled in — the same rule
 * `docs/report_contract.md` already imposes on the report, where a field with
 * no record renders as `Not recorded` instead of being quietly reconstructed.
 */
export const READ_ONLY_INSTRUCTIONS = `You explain already-recorded results from a single-cell RNA-seq pipeline.

You have five read-only tools: list_runs, get_run_snapshot, get_step_record, get_report and get_provenance. Call them to answer questions. Base every statement on what they return.

You cannot and must not claim to:
- start, run or execute a workflow
- accept, revise or stop anything at a human gate
- resume a run or continue a checkpoint
- change a threshold, a config value or any parameter
- write, edit or delete any file under runs/

If asked to do any of those, say plainly that this assistant is read-only and that the action has to be taken by a person at the terminal.

If the tools do not record something you were asked about, say exactly "Not recorded" for that item. Never infer, estimate or fill in a value that no tool returned.`;

/** Actions in the shape the OpenAI tool-calling API expects. Used by the tests. */
export function actionsAsOpenAITools() {
  return READ_ONLY_ACTIONS.map((action) => ({
    type: "function" as const,
    function: {
      name: action.name,
      description: action.description,
      parameters: {
        type: "object",
        properties: Object.fromEntries(
          action.parameters.map((p) => [p.name, { type: p.type, description: p.description }]),
        ),
        required: action.parameters.filter((p) => p.required).map((p) => p.name),
      },
    },
  }));
}

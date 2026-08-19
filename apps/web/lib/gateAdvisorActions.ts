import "server-only";
import { gateBriefing } from "./gateContext.ts";
import { getReport, getStepRecords } from "./gateway.ts";

/**
 * The advisor that sits beside a paused gate, and the line it cannot cross.
 *
 * A person looking at sixty-one CellTypist models can see what each was trained
 * on and still not know which fits *their* data. That is a real gap and it is
 * not a UI problem — no arrangement of a list answers "which of these suits a
 * human PBMC sample whose clusters express S100A8, IGHD and FCGR3A". Answering
 * it needs someone who can read the marker signature and the model catalogue
 * together, which is what a language model is genuinely good at.
 *
 * So this exists. And the whole reason it is safe to add is that it changes
 * nothing about who decides.
 *
 * ## What it cannot do
 *
 * There is no action here that answers a gate, submits a decision, or writes a
 * parameter — the same rule as everywhere else in this app, enforced the same
 * way: by what was put within reach. `accept`, `revise` and `stop` are a POST
 * from a Route Handler behind a button, and nothing in this file can reach it.
 *
 * The model may argue for an option. A person picks it in the list, and a
 * person presses Submit. `docs/copilotkit_product_architecture.md` §1.4: a
 * judge may score and advise, an agent may explain evidence or propose a value,
 * neither can answer.
 *
 * That boundary is also why the advice can be blunt. An advisor that could act
 * on its own opinion would need hedging; one that can only argue is free to
 * make the strongest honest case and let somebody weigh it.
 */

export type AdvisorAction = {
  name: string;
  description: string;
  parameters: { name: string; type: "string"; description: string; required: boolean }[];
  handler: (args: Record<string, unknown>) => Promise<unknown>;
};

function runIdOf(args: Record<string, unknown>): string | null {
  const value = typeof args.run_id === "string" ? args.run_id.trim() : "";
  return value || null;
}

export const GATE_ADVISOR_ACTIONS: AdvisorAction[] = [
  {
    name: "get_gate_briefing",
    description:
      "The decision this run is paused on: what it is asking, every option it recorded (with " +
      "descriptions and whether each is already downloaded), and what is known about this data — " +
      "species, cluster count, and the top marker genes of each cluster. Call this first; it is " +
      "what the recommendation has to be argued from.",
    parameters: [
      { name: "run_id", type: "string", description: "the scientific run id", required: true },
    ],
    handler: async (args) => {
      const runId = runIdOf(args);
      if (!runId) return { error: "run_id is required" };
      return gateBriefing(runId);
    },
  },
  {
    name: "get_step_record",
    description:
      "Every step's status, judge verdict, recorded settings and metrics for one run. Use it " +
      "when the choice turns on something earlier in the analysis — how many cells survived QC, " +
      "what clustering resolution produced these clusters, whether integration ran.",
    parameters: [
      { name: "run_id", type: "string", description: "the scientific run id", required: true },
    ],
    handler: async (args) => {
      const runId = runIdOf(args);
      if (!runId) return { error: "run_id is required" };
      return (await getStepRecords(runId)) ?? { error: `no run ${runId}` };
    },
  },
  {
    name: "get_report",
    description:
      "The saved report for one run, if it has been produced. Usually absent at a gate — a run " +
      "that is still paused has not reached build_report — so an empty answer here is expected " +
      "rather than a problem.",
    parameters: [
      { name: "run_id", type: "string", description: "the scientific run id", required: true },
    ],
    handler: async (args) => {
      const runId = runIdOf(args);
      if (!runId) return { error: "run_id is required" };
      return (await getReport(runId)) ?? { error: `no run ${runId}` };
    },
  },
];

/**
 * The advisor's brief.
 *
 * Written to produce an argument rather than a summary. The failure mode this
 * guards against is not the model being wrong — it is the model restating the
 * sixty-one descriptions the person can already read, and closing with "it
 * depends on your research question", which is true and useless.
 */
export const GATE_ADVISOR_INSTRUCTIONS = `You advise on one decision: the human gate a single-cell RNA-seq run is currently paused at. The person reading you has the list of options on screen and cannot tell which one fits their data. That is the gap you exist to close.

Start by calling get_gate_briefing with the run id. It gives you the question, every option with its description and whether it is downloaded, and the evidence that bears on the choice — species, cluster count, and the top marker genes of each cluster.

How to answer:
- **Recommend one option, first sentence.** Then say why, citing specific evidence: the marker genes, the species, the cluster count. "Clusters expressing S100A8, S100A9 and LYZ are monocytes; IGHD and FCRL1 are B cells" is an argument. "It depends on your research question" is not.
- **Name the closest runner-up and say what would make it the better choice.** That is what lets somebody disagree with you for a reason.
- **Say when an option is already downloaded.** Choosing one that is not is a decision to wait for a download, and it should be a deliberate one.
- **Say when you cannot tell.** If the markers are ambiguous, or the evidence does not distinguish two options, say so and say what would.
- Be concise. A paragraph of recommendation and a short comparison beats an essay.

What you must not do:
- You cannot answer the gate. accept, revise and stop are the operator's, always. Never say you have selected, applied, submitted or set anything.
- You cannot change a threshold, a parameter or any scientific result.
- Never invent an option. Recommend only from the list get_gate_briefing returned; if something a person asks about is not in it, say it is not among the recorded candidates.
- Never claim a marker gene or a number the tools did not return. If you need evidence you do not have, say which.
- If the tools show no pending gate, say the run is not waiting on a decision rather than advising on a hypothetical one.

The person picks the option in the list beside you and presses Submit. Your job is to make that choice an informed one, not to make it for them.`;

/** Actions in the shape the OpenAI tool-calling API expects. Used by the tests. */
export function advisorActionsAsOpenAITools() {
  return GATE_ADVISOR_ACTIONS.map((action) => ({
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

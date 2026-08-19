import "server-only";
import {
  getAnalysisRequestStatus,
  listCatalog,
  previewAnalysisRequest,
  type PreviewInput,
} from "./controller.ts";

/**
 * The four actions an intake assistant may call, and the one it may not.
 *
 * ## The one it may not
 *
 * There is no `confirm_analysis_request` here, and there must never be. A model
 * that can prepare a request and also confirm it is a model that starts
 * analyses — the human step becomes a formality it can perform on the user's
 * behalf. Confirmation is a POST from
 * `app/api/analysis-requests/[requestId]/confirm/route.ts`, reached by a button
 * in `components/AnalysisIntake.tsx`, and nothing in this file can reach it.
 *
 * The same holds for the gate: `accept`, `revise` and `stop` are not actions.
 * `docs/copilotkit_product_architecture.md` §1.4 — a judge may score, an agent
 * may explain, neither may answer.
 *
 * ## What these four can do
 *
 * Look things up, and produce a draft. `prepare_analysis_request` posts to the
 * controller's preview endpoint, which by construction creates no run
 * directory and queues no job — so the worst a confused assistant can do is put
 * a wrong draft on a screen next to a disabled button.
 *
 * As with the read-only actions, the boundary is what these functions can
 * reach, not the wording of the instructions below. The instructions exist so
 * the model does not *describe* itself as having done something it cannot do.
 */

export type IntakeAction = {
  name: string;
  description: string;
  parameters: {
    name: string;
    type: "string" | "number" | "boolean" | "object";
    description: string;
    required: boolean;
  }[];
  handler: (args: Record<string, unknown>) => Promise<unknown>;
};

function text(value: unknown): string | null {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Read the model's `analysis` argument, which may arrive as a JSON string.
 *
 * Tool-calling models frequently send an object parameter as text. Parsing it
 * here is a convenience, not a widening: whatever comes out is sent to the
 * controller, which accepts only the names in its own allowlist and reports
 * every other key as a validation error.
 */
function analysisArg(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object") return value as Record<string, unknown>;
  if (typeof value === "string" && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object") return parsed as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  return {};
}

export const INTAKE_ACTIONS: IntakeAction[] = [
  {
    name: "list_available_datasets",
    description:
      "List the datasets this server can analyse. Returns each one's input_ref, display name, " +
      "and the kind of data the catalog says it is. Never returns a filesystem path.",
    parameters: [],
    handler: async () => (await listCatalog()).datasets,
  },
  {
    name: "list_available_study_designs",
    description:
      "List the study manifests this server offers. A manifest says which sequencing library " +
      "came from which sample, donor, condition and technical batch. Needed before batch " +
      "correction, and before any question about differences between samples.",
    parameters: [],
    handler: async () => (await listCatalog()).study_designs,
  },
  {
    name: "prepare_analysis_request",
    description:
      "Validate a proposed analysis and produce a structured draft. This is a preview only: it " +
      "creates no run, queues no job and starts nothing. Returns the draft, the questions that " +
      "are still unanswered, any validation errors, anything asked for that this workflow does " +
      "not support, and the plan of what would be executed. Call it again with the same " +
      "request_id to revise the same draft as the conversation fills gaps in.",
    parameters: [
      { name: "request_id", type: "string", required: false,
        description: "Revise an existing draft instead of starting a new one." },
      { name: "input_ref", type: "string", required: false,
        description: "A dataset reference from list_available_datasets, e.g. dataset:pbmc_1k_v3." },
      { name: "input_path", type: "string", required: false,
        description:
          "A location the user named. Passed to the server for validation against its allowlist; " +
          "it is replaced by a reference and never used as given. Do not invent one." },
      { name: "input_kind_hint", type: "string", required: false,
        description:
          "What the user says the data is (fastq, matrix, h5ad). A hint only — ingest_validate " +
          "detects the real type from the filesystem and its answer wins." },
      { name: "species", type: "string", required: false,
        description: "The species, e.g. human or mouse." },
      { name: "research_question", type: "string", required: false,
        description: "What the analysis is for, in the user's own words. Required before confirmation." },
      { name: "project", type: "string", required: false,
        description: "A short name for the analysis; the report titles itself with it." },
      { name: "study_design_ref", type: "string", required: false,
        description: "A manifest reference from list_available_study_designs." },
      { name: "analysis", type: "object", required: false,
        description:
          "Analysis settings, using only these names: embedding_method, embedding_dimensions, " +
          "embedding_max_cells, integration_mode, resolution, celltypist_model, " +
          "scmayomap_tissue, random_state, min_genes, min_counts, max_pct_mito, " +
          "remove_doublets. Any other key is rejected by the server." },
    ],
    handler: async (args) => {
      const input: PreviewInput = {
        request_id: text(args.request_id),
        input_ref: text(args.input_ref),
        input_path: text(args.input_path),
        input_kind_hint: text(args.input_kind_hint),
        species: text(args.species),
        research_question: text(args.research_question),
        project: text(args.project),
        study_design_ref: text(args.study_design_ref),
        analysis: analysisArg(args.analysis),
      };
      const preview = await previewAnalysisRequest(input);
      return {
        ...preview,
        // Said back to the model in the response itself, so the fact travels
        // with the data rather than depending on the system prompt being
        // recalled at the end of a long conversation.
        reminder:
          "Nothing has been started. The user must press Confirm in the page for this " +
          "analysis to run. You cannot press it and must not say the analysis has begun.",
      };
    },
  },
  {
    name: "get_analysis_request",
    description:
      "Read the current state of a request: its status, the scientific run it became if it has " +
      "been confirmed, and whether that run is waiting for a human decision. Read-only.",
    parameters: [
      { name: "request_id", type: "string", required: true, description: "The analysis request id." },
    ],
    handler: async (args) => {
      const id = text(args.request_id);
      if (!id) return { error: "request_id is required" };
      return getAnalysisRequestStatus(id);
    },
  },
];

/**
 * The intake contract, stated to the model.
 *
 * Defence in depth. The control is that no function above can confirm anything
 * or answer a gate. This text exists so the model does not claim otherwise, and
 * so it asks instead of inventing — a filled-in species or manifest produces a
 * request that looks complete and describes an analysis nobody asked for.
 */
export const INTAKE_INSTRUCTIONS = `You are the analysis intake assistant for a single-cell RNA-seq pipeline. You help a scientist turn what they want into a validated analysis request. You are not an executor.

Your tools: list_available_datasets, list_available_study_designs, prepare_analysis_request, get_analysis_request.

How to work:
- Establish four things before a request can be confirmed: which data (a dataset reference, or a location the server can validate), the species, the research question in the user's own words, and — if the question is about differences between samples, or if batch correction is wanted — which study manifest describes the libraries.
- Ask for anything you do not have. Never fill in a species, a manifest, a CellTypist model or a threshold that the user did not give you. A guessed value produces a request that looks complete and describes an analysis nobody asked for.
- Call prepare_analysis_request as the conversation fills gaps, reusing the same request_id, so the draft on screen always matches what has been agreed.
- Report the server's missing_questions, validation_errors and unsupported list back to the user plainly. They are the answer, not an obstacle.

What you cannot do:
- You cannot start, confirm or queue an analysis. Only the user pressing Confirm in this page does that. Never say an analysis has started, is running, or has been submitted because of something you did.
- You cannot answer a human gate. accept, revise and stop are the operator's, always.
- You cannot change a threshold, a config value or a scientific result.
- You cannot run a shell command, execute code, or name a file for the server to open outside its own allowlist.
- You must never ask the user to paste an API key or a credential.

What this workflow supports: quality control and filtering, doublet detection, normalisation and highly-variable genes, PCA, optional Harmony batch correction, Leiden clustering, UMAP and t-SNE embeddings, marker genes, CellTypist annotation, and a marker-database cross-check, ending in a report.

What it does not support: trajectory inference or pseudotime, RNA velocity, differential expression testing between conditions, cell-cell communication, and copy-number inference. If the user asks for one of these, say plainly that this pipeline has no step for it and do not present a request as though it covered it. Comparing cell type composition across samples is done by reading the per-sample labels in the report, and it needs a study manifest — it is not a statistical test this pipeline runs.

If data does not exist, a reference is not valid, or a request is outside what the workflow can do, say so and leave the request in preview. An honest refusal is the correct outcome.`;

/** Actions in the shape the OpenAI tool-calling API expects. Used by the tests. */
export function intakeActionsAsOpenAITools() {
  return INTAKE_ACTIONS.map((action) => ({
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

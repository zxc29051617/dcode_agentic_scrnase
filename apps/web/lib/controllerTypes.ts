/**
 * The shapes the analysis controller returns.
 *
 * Separate from `lib/controller.ts` for the same reason `gatewayTypes.ts` is
 * separate from `gateway.ts`: that module starts with `import "server-only"`,
 * so importing it from a Client Component is a build error, and the draft card
 * is a Client Component that needs to name these.
 *
 * Every field name here is the same string in
 * `services/controller/app/domain.py` and in
 * `schemas/analysis_request.schema.json`. One vocabulary, three places that
 * have to agree, and no translation layer between them where a rename could
 * hide.
 */

export type MissingQuestion = {
  field: string;
  question: string;
  required: boolean;
  options: unknown[];
};

export type AnalysisSettings = {
  embedding_method?: "umap" | "tsne" | "both";
  embedding_dimensions?: (2 | 3)[];
  embedding_max_cells?: number;
  integration_mode?: "none" | "harmony";
  resolution?: number;
  celltypist_model?: string;
  scmayomap_tissue?: string;
  random_state?: number;
  min_genes?: number;
  min_counts?: number;
  max_pct_mito?: number;
  remove_doublets?: boolean;
};

export type RequestStatus =
  | "draft"
  | "validated"
  | "awaiting_confirmation"
  | "queued"
  | "running"
  | "needs_review"
  | "completed"
  | "failed"
  | "cancelled"
  | "rejected";

export type AnalysisRequest = {
  request_id: string;
  conversation_id: string | null;
  input_ref: string | null;
  project: string | null;
  species: string | null;
  research_question: string | null;
  study_design_ref: string | null;
  analysis: AnalysisSettings;
  status: RequestStatus;
  config_digest: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  missing_questions: MissingQuestion[];
  validation_errors: string[];
  warnings: string[];
  unsupported: string[];
  scientific_run_id: string | null;
};

export type ExecutionPlan = {
  route: "fastq" | "matrix" | "h5ad" | "unknown";
  /** Always true. `ingest_validate` decides the route at run time; this is display only. */
  route_is_provisional: boolean;
  route_decided_by: "ingest_validate";
  steps: string[];
  mainline?: string[];
  excluded_by_route?: string[];
  gates: { step: string; why: string }[];
  estimated_gates: number | null;
  note?: string;
};

export type PreviewResponse = {
  request: AnalysisRequest;
  /** The server's answer, not the browser's. The button follows it. */
  can_confirm: boolean;
  executor_config_preview: Record<string, unknown>;
  execution_plan: ExecutionPlan;
};

export type ConfirmResponse = {
  request_id: string;
  job_id: string;
  scientific_run_id: string;
  status: string;
  idempotent_replay: boolean;
};

export type JobView = {
  job_id: string;
  kind: string;
  status: string;
  scientific_run_id: string | null;
  error: string | null;
} | null;

/**
 * What a run is waiting on, as the controller reads it from the run's own
 * audit log. `generation` is how many gates this run has opened; a decision
 * carries it back so an answer to a question that has since been superseded is
 * refused rather than applied.
 */
export type GateState = {
  scientific_run_id: string;
  status: "queued" | "running" | "needs_review" | "completed";
  generation: number;
  gate_id: string | null;
  pending_gate: {
    gate: string;
    step: string;
    revise_target: string;
    revisable: string[];
    verdict: string | null;
    score: number | null;
    reasons: string[];
    suggested_action: string | null;
    advice: { parameter?: string; suggested_value?: unknown; confidence?: string; rationale?: string }[];
    evidence: Record<string, unknown>;
    review?: Record<string, unknown>;
  } | null;
  has_report: boolean;
};

export type RequestStatusView = {
  request_id: string;
  status: RequestStatus;
  scientific_run_id: string | null;
  job: JobView;
  run: GateState | null;
};

export type DatasetOption = {
  input_ref: string;
  display_name: string;
  kind: string;
  species_hint: string | null;
  description: string | null;
};

export type StudyDesignOption = {
  study_design_ref: string;
  display_name: string;
  description: string | null;
};

export type DecisionResponse = {
  decision_id: string;
  job_id: string;
  scientific_run_id: string;
  gate_id: string;
  generation: number;
  accepted_overrides: Record<string, unknown>;
  status: string;
};

/**
 * The catalog as the controller projects it.
 *
 * `rejected` names the entries the catalog file offered and the allowlist
 * refused, with a reason and *no path*. It exists because a dropped entry used
 * to be silent: a mistyped path produced an empty dataset list, which is
 * indistinguishable from a catalog nobody has filled in yet.
 */
export type CatalogView = {
  datasets: DatasetOption[];
  study_designs: StudyDesignOption[];
  rejected: { name: string; reason: string }[];
};

/**
 * One species the pipeline has vetted constants for.
 *
 * `reference_present` and the profile are deliberately separate. "This
 * pipeline knows human" and "this machine has the human reference" are
 * different facts, and an intake form that shows only the first offers a run
 * that will stop at `resolve_reference` with nothing to resolve.
 */
export type SpeciesProfileView = {
  species: string;
  reference_dirname: string;
  reference_present: boolean;
  note: string;
  /** `prebuilt` when 10x ships a tarball, `build` when it has to be made. */
  how: "prebuilt" | "build";
  download_gb: number | null;
  disk_gb: number | null;
  /** PanglaoDB column, or null — the marker cross-check degrades without one. */
  marker_db: string | null;
  /** False means the QC starting points were read off another species' data. */
  qc_defaults_native: boolean;
};

/**
 * What the intake may say about species before anybody commits to one.
 *
 * `available: false` means the controller could not import the scientific
 * package, so this is "unknown", not "nothing is supported". The UI has to say
 * which, because those call for opposite actions.
 */
export type SpeciesCatalogView = {
  available: boolean;
  profiled: SpeciesProfileView[];
  /** Names the pipeline understands but has no vetted gene lists for. */
  recognised: string[];
  gtf_requirements: { requirement: string; why: string }[];
};

/**
 * The shapes the gateway returns.
 *
 * Separate from `lib/gateway.ts` because that module starts with
 * `import "server-only"` — importing it from a Client Component is a build
 * error, which is exactly the guard we want on the code that knows
 * `GATEWAY_URL`. Types carry no runtime code and no secret, so they live
 * here where a table or a timeline can name them.
 */

export type RunSummary = {
  scientific_run_id: string;
  status: string;
  started_at: string | null;
  /**
   * When this run last wrote anything, UTC ISO-8601, or null.
   *
   * The evidence behind a `running` or `interrupted` verdict. The gateway
   * cannot see processes — it reads files — so it reports what it judged on
   * rather than asking to be taken at its word.
   */
  last_activity_at: string | null;
  /** The step a run is sitting inside, when it is sitting inside one. Present
   *  in the list as well as the detail, because the list is where somebody
   *  decides which run to open. */
  unfinished_step: string | null;
  /** The step an open gate is asking about. Distinct from `unfinished_step`:
   *  a gate opens after its step has already ended, so a run waiting for a
   *  person has one of these and not the other. */
  pending_gate_step: string | null;
  steps_recorded: number;
  cells: number | null;
  clusters: number | null;
  cell_types: number | null;
};

export type StepEntry = {
  step: string;
  status: string;
  verdict: string | null;
};

export type PendingGate = {
  gate: string;
  step: string;
  revise_target: string;
  revisable: string[];
  verdict: string | null;
  score: number | null;
  reasons: string[];
  suggested_action: string | null;
  advice: unknown[];
  evidence: Record<string, unknown>;
} | null;

export type RunSnapshot = {
  scientific_run_id: string;
  status: string;
  started_at: string | null;
  species: string | null;
  steps: StepEntry[];
  pending_gate: PendingGate;
  last_activity_at: string | null;
  /** The step a run stopped inside, when it stopped inside one. Where
   *  `--resume-from` would pick it up. */
  unfinished_step: string | null;
  /** When the step it is currently inside began, or null if it is not inside one. */
  current_step_started_at: string | null;
  /** How long it has been in that step. Null when no step is open. */
  current_step_elapsed_seconds: number | null;
  has_report: boolean;
  warn_count: number;
  fail_count: number;
  reused_steps: string[];
  cells: number | null;
  clusters: number | null;
  cell_types: number | null;
};

/**
 * The upstream QC numbers FastQC and Cell Ranger recorded, as the gateway
 * projects them. Present only on `fastq_qc` and `cellranger_count`; every
 * other step omits the key entirely.
 *
 * No field here is a filesystem path. The gateway drops `outs`, `bam`,
 * `web_summary`, `report_dir` and `multiqc_report`, keeping only whether a
 * report exists — serving the files themselves needs an artifact endpoint
 * that does not exist yet.
 */
export type UpstreamDetail = {
  // fastq_qc
  per_read_role?: Record<
    string,
    {
      n_files: number;
      q30_fraction: number | null;
      duplicate_fraction: number | null;
      total_sequences: number;
    }
  >;
  files?: {
    file: string;
    read_role: string | null;
    total_sequences: number | null;
    sequence_length: string | null;
    pct_gc: string | null;
    q30_fraction: number | null;
    duplicate_fraction: number | null;
    max_adapter_pct: number | null;
    modules_failed: string[];
    modules_warned: string[];
  }[];
  files_total?: number;
  files_shown?: number;
  module_failures?: Record<string, string[]>;
  expected_module_flags?: string[];
  notes?: string[];
  has_multiqc_report?: boolean;

  // cellranger_count
  libraries?: {
    library_id: string | null;
    chemistry: string | null;
    metrics_summary: Record<string, string>;
    has_web_summary: boolean;
  }[];
};

/**
 * One servable file inside a run, as the gateway's manifest lists it.
 *
 * `artifact_id` is an opaque token the manifest produced — not an encoded
 * path — and it is the only thing the content endpoint accepts.
 * `relative_path` is inside the run and safe to display; no absolute host
 * path appears anywhere in this shape.
 */
export type ArtifactEntry = {
  artifact_id: string;
  kind:
    | "fastqc_html"
    | "multiqc_html"
    | "cellranger_web_summary"
    | "report_html"
    | "embedding_html"
    | "embedding_json"
    | "report_pdf"
    | "figure";
  label: string;
  name: string;
  relative_path: string;
  media_type: string;
  size_bytes: number;
  too_large: boolean;
  is_html: boolean;
};

export type StepRecord = {
  step: string;
  status: string;
  verdict: { verdict: string; score: number; reasons: string[] } | null;
  output_summary: {
    warnings: string[];
    errors: string[];
    metrics: Record<string, unknown>;
  };
  /**
   * What the step said about its own result, in its own words.
   *
   * Distinct from a judge verdict and from a warning. `run_clustering` records
   * "the smallest cluster has only 8 cells; may be noise rather than a
   * population" and is still judged `pass`, because the judge is asked whether
   * the step ran soundly and by that measure it did.
   */
  notes?: string[];
  /** How long this step took, from its own audit pair. Absent for a step that
   *  has not ended — still running, or the run died inside it. Neither of
   *  those is "took no time". */
  duration_seconds?: number | null;
  /** How the step ran: its settings, thresholds and choices. Never a path. */
  settings?: Record<string, unknown>;
  /** Figures this step's numbers produced, by artifact id. */
  figures?: { artifact_id: string; name: string; label: string }[];
  upstream_detail?: UpstreamDetail;
};

export type ReportView = {
  available: boolean;
  reason: string | null;
  format: string | null;
  content: string | null;
  source_path: string | null;
};

export type Provenance = {
  scientific_run_id: string;
  source: Record<string, unknown>;
  packages: Record<string, string>;
  seeds: Record<string, unknown>;
  study_design: Record<string, unknown>;
  judge_sessions: unknown[];
  revisions: unknown[];
};

/**
 * How long each step has actually taken on this machine.
 *
 * Every duration shown to a person comes from here. `steps` omits any step the
 * gateway has fewer than `min_runs_required` finished runs for, so an absent
 * entry means "not measured yet" and never "instant" — the UI has to say the
 * first rather than imply the second.
 */
export type StepTimings = {
  steps: Record<
    string,
    { n: number; median_seconds: number; min_seconds: number; max_seconds: number }
  >;
  runs_measured: number;
  min_runs_required: number;
  total_median_seconds: number | null;
};

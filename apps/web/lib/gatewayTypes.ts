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

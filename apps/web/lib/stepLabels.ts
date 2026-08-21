/**
 * What each step is called when a person reads it, and what it actually does.
 *
 * The workflow page used to render the executor's own function names — 26 rows
 * of `count_matrix_classify` and `normalize_hvg_prepare`. Those are the right
 * names in `src/registry.py`, where the reader is someone changing the code.
 * They are the wrong names on a screen shown to somebody deciding whether to
 * accept a result, because reading them requires already knowing the pipeline,
 * which is exactly what the person at a gate does not have.
 *
 * Both are kept. The plain title is what the eye lands on; the function name
 * stays beside it in a `<code>`, because it is what appears in `audit.jsonl`,
 * in `--resume-from`, and in every message the executor writes. Replacing it
 * would make the screen unusable for the person debugging a run.
 *
 * ## Why these strings live here and not in the registry
 *
 * `StepSpec` carries what the executor needs — kind, judge, revisable keys.
 * Adding UI copy to it would put a sentence about wording inside the module
 * every skill imports, and would mean editing the scientific package to fix a
 * typo on a web page.
 *
 * The cost of a second list is that it can fall behind, so it is not allowed
 * to: `tests/test_step_labels.py` reads `src/registry.py` and fails if any
 * step has no entry here, or if an entry names a step the registry does not
 * have. A step added to the pipeline breaks that test until somebody writes
 * the sentence a person will read.
 */

export type StepLabel = {
  /** What a person reads. Sentence case, no jargon, no function name. */
  title: string;
  /**
   * One sentence: what this step does to the data.
   *
   * Present tense, active, and about the data rather than the code — "removes
   * cells that look like debris", not "applies the QC filter". Somebody at a
   * gate is deciding about the data, not about the function.
   */
  what: string;
  /**
   * The stage this belongs to, for grouping a long list into a shape the eye
   * can hold. Four groups, matching how the analysis is actually described in
   * a methods section.
   */
  stage: "input" | "counting" | "quality" | "structure" | "identity" | "output";
};

export const STEP_LABELS: Record<string, StepLabel> = {
  // --- getting the data in ---------------------------------------------------
  ingest_validate: {
    title: "Look at what was given",
    what: "Works out what kind of data this is — raw reads, a count matrix, or a Cell Ranger output folder — and which route the rest of the run takes.",
    stage: "input",
  },
  sample_qc_triage: {
    title: "Read the supplied QC table",
    what: "Reads per-sample quality numbers that came with the data, when there are any, so a bad library is known about before it is analysed.",
    stage: "input",
  },
  resolve_reference: {
    title: "Find the reference genome",
    what: "Locates the genome and gene annotation for this species, and checks the version string matches what the run expects.",
    stage: "input",
  },
  matrix_preflight: {
    title: "Check the matrix is readable",
    what: "Opens the count matrix far enough to confirm it is intact and to see how many cells and genes it holds.",
    stage: "input",
  },

  // --- from reads to counts --------------------------------------------------
  fastq_preflight: {
    title: "Check the sequencing files",
    what: "Confirms every read file is present and paired, and identifies the 10x chemistry from the barcodes rather than from the file names.",
    stage: "counting",
  },
  fastq_qc: {
    title: "Quality of the raw reads",
    what: "Runs FastQC over the sequencing files and reports read quality, adapter content and duplication before anything is aligned.",
    stage: "counting",
  },
  cellranger_count: {
    title: "Align reads and count genes",
    what: "Runs Cell Ranger: aligns every read to the genome and produces the count matrix. This is the long step — tens of minutes, and it writes nothing while it works.",
    stage: "counting",
  },
  count_matrix_classify: {
    title: "Raw or already filtered?",
    what: "Decides whether the matrix still contains empty droplets or has already had cells called, because the two need different handling.",
    stage: "counting",
  },

  // --- deciding which droplets are cells --------------------------------------
  load_raw_counts: {
    title: "Load the unfiltered matrix",
    what: "Reads a matrix that still contains every droplet, including the empty ones.",
    stage: "quality",
  },
  load_filtered_counts: {
    title: "Load the called cells",
    what: "Reads a matrix where cells have already been called from the background.",
    stage: "quality",
  },
  cell_calling_review: {
    title: "Check which droplets are cells",
    what: "Looks at the barcode-rank curve to see whether the cell/empty boundary was drawn somewhere defensible.",
    stage: "quality",
  },
  merge_samples: {
    title: "Combine the samples",
    what: "Joins several libraries into one dataset, labelling every cell with the sample it came from and refusing to merge ones built on different genomes.",
    stage: "quality",
  },
  post_load_validate: {
    title: "Sanity-check the loaded data",
    what: "Confirms the matrix that was loaded is the shape the next steps assume — no duplicate barcodes, no empty genes, counts that are actually integers.",
    stage: "quality",
  },
  run_qc_metrics: {
    title: "Measure cell quality",
    what: "Computes per-cell numbers: how many genes, how many counts, and what fraction is mitochondrial — the signal that a cell was dying when it was captured.",
    stage: "quality",
  },
  apply_cell_qc_filter: {
    title: "Remove low-quality cells",
    what: "Drops cells that fall outside the chosen thresholds. This is destructive and cannot be undone later in the run, which is why it stops to ask.",
    stage: "quality",
  },
  detect_doublets: {
    title: "Find droplets with two cells",
    what: "Flags barcodes whose expression looks like two cells captured together, which otherwise appear as a cell type that does not exist.",
    stage: "quality",
  },

  // --- finding structure ------------------------------------------------------
  normalize_hvg_prepare: {
    title: "Normalise and pick informative genes",
    what: "Puts every cell on a comparable scale and selects the genes that vary between cells, which are the ones structure can be found in.",
    stage: "structure",
  },
  run_pca: {
    title: "Reduce to principal components",
    what: "Compresses thousands of genes into a few dozen components that carry most of the variation, so distances between cells become meaningful.",
    stage: "structure",
  },
  run_integration: {
    title: "Correct for batch effects",
    what: "Removes differences caused by when and how libraries were prepared, using the technical batch stated in the study manifest and nothing else.",
    stage: "structure",
  },
  run_clustering: {
    title: "Group similar cells",
    what: "Builds a neighbour graph and partitions it into clusters. The resolution decides how finely — it is a choice, not a discovered truth.",
    stage: "structure",
  },
  run_umap: {
    title: "Lay the cells out for viewing",
    what: "Computes a two- or three-dimensional layout so the clusters can be looked at. Distances on this plot are for viewing, not for measuring.",
    stage: "structure",
  },

  // --- saying what the cells are ----------------------------------------------
  find_markers: {
    title: "Find each cluster's marker genes",
    what: "Works out which genes are raised in each cluster relative to the rest, which is the evidence any cell-type label rests on.",
    stage: "identity",
  },
  annotate_cells: {
    title: "Label the cell types",
    what: "Assigns a cell type to each cluster using a CellTypist model. A model trained on the wrong tissue returns confident wrong labels rather than failing, so the model is chosen deliberately.",
    stage: "identity",
  },
  cross_check_annotation: {
    title: "Check the labels against a marker database",
    what: "Scores the assigned labels against an independent marker database, so a disagreement surfaces here rather than in a figure.",
    stage: "identity",
  },

  // --- finishing ---------------------------------------------------------------
  human_review_decision: {
    title: "Your decision",
    what: "The run has paused and is waiting for a person to accept the result, change a setting and redo the step, or stop.",
    stage: "output",
  },
  build_report: {
    title: "Write the report",
    what: "Assembles the figures and numbers already recorded into a report. It computes nothing new — everything in it was decided by a step above.",
    stage: "output",
  },
};

/** The stage headings, in pipeline order. */
export const STAGE_ORDER: StepLabel["stage"][] = [
  "input", "counting", "quality", "structure", "identity", "output",
];

export const STAGE_TITLES: Record<StepLabel["stage"], string> = {
  input: "What was given",
  counting: "Reads to counts",
  quality: "Which cells to keep",
  structure: "Finding structure",
  identity: "What the cells are",
  output: "Result",
};

/**
 * A step's label, or a readable fallback.
 *
 * The fallback un-snake-cases rather than showing the raw name, so a step this
 * file has not caught up with still reads as English. It is deliberately not a
 * thrown error: a new step in the executor must not blank the page of a run
 * that is otherwise fine. The test is what makes sure it does not stay missing.
 */
export function stepLabel(step: string): StepLabel {
  return (
    STEP_LABELS[step] ?? {
      title: step.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
      what: "",
      stage: "structure",
    }
  );
}

/**
 * The reviewer's verdict, in words that say what it means for the reader.
 *
 * "Reviewer" on screen, `judge` in the code and in every audit log. The two are
 * deliberately different: `judge` is the executor's own name for it and appears
 * in `judge_sessions`, in `judge_*` tool names and in the recorded events of
 * every run that has already finished, so renaming the field would make this
 * projection disagree with what is on disk. What a person reads is a separate
 * decision from what the record calls it, and "judge" reads as a courtroom for
 * something that is closer to peer review — it examines a result and says what
 * it thinks, and it decides nothing.
 *
 * `pass` / `warn` / `fail` are the executor's vocabulary and stay in the data.
 * On screen they are ambiguous in a specific way: a person reads "warn" as
 * "something is wrong", when what the reviewer means is "this ran soundly and
 * here is something you should look at". The distinction matters because the
 * gate asks them to decide.
 */
export const VERDICT_WORDS: Record<string, { word: string; meaning: string }> = {
  pass: { word: "Looks sound", meaning: "The reviewer found nothing to raise." },
  warn: {
    word: "Worth a look",
    meaning: "The step ran soundly and the reviewer has something for you to see. It is not an error.",
  },
  fail: {
    word: "Needs attention",
    meaning: "The reviewer does not think this result should be built on as it stands.",
  },
};

/** Run status, in words a person can act on. */
export const STATUS_WORDS: Record<string, { word: string; meaning: string }> = {
  running: { word: "Running", meaning: "Working now." },
  needs_review: { word: "Waiting for you", meaning: "Paused at a decision only a person can make." },
  interrupted: {
    word: "Stopped unexpectedly",
    meaning: "Nothing has been written for a while and no step finished. The process is probably gone.",
  },
  completed: { word: "Finished", meaning: "Ran to the end and wrote a report." },
  failed: { word: "Failed", meaning: "A step could not complete." },
  halted: { word: "Stopped by you", meaning: "Someone chose to stop this run." },
  queued: { word: "Queued", meaning: "Accepted, waiting for a worker to pick it up." },
};

export function statusWord(status: string): string {
  return STATUS_WORDS[status]?.word ?? status.replace(/_/g, " ");
}

/** A step record's status, rendered for the page rather than the audit log. */
export function stepStatusWord(status: string): string {
  switch (status) {
    case "ok":
    case "done":
    case "completed":
      return "Completed";
    case "skipped":
      return "Skipped";
    case "error":
    case "failed":
      return "Failed";
    case "running":
      return "Running";
    default:
      return status.replace(/_/g, " ");
  }
}

/** Request statuses from the controller, for the intake page. */
export function requestStatusWord(status: string): string {
  switch (status) {
    case "draft":
      return "Draft";
    case "validated":
      return "Ready";
    case "awaiting_confirmation":
      return "Waiting for confirmation";
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "needs_review":
      return "Waiting for you";
    case "completed":
      return "Finished";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    case "rejected":
      return "Rejected";
    default:
      return status.replace(/_/g, " ");
  }
}

/** Job statuses from the controller, for the intake page's started panel. */
export function jobStatusWord(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "waiting":
      return "Waiting at gate";
    case "completed":
      return "Finished";
    case "failed":
      return "Failed";
    default:
      return status.replace(/_/g, " ");
  }
}

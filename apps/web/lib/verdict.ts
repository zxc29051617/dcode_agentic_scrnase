/**
 * One vocabulary for how a step or a run went, shared by every surface.
 *
 * The run list, the summary cards and the workflow timeline all colour the
 * same four outcomes. Deriving that mapping separately in each component is
 * how `warn` ends up amber in one place and red in another, which teaches a
 * reader that the colour means nothing.
 *
 * Safe to import from a Client Component: no `server-only`, no environment,
 * no gateway.
 */

export type Tone = "pass" | "warn" | "fail" | "reused" | "running" | "muted";

/**
 * What a stored value is called on screen.
 *
 * The mapping is presentation only and it stops here. `prompts/`, `schemas/`
 * and the judge's return value keep `pass` / `warn` / `fail`: all nine prompt
 * files instruct the model in terms of `warn`, their sha256 is recorded in
 * `run_metadata.json`, and editing them would mean re-measuring the eight step
 * prompts that have been measured. The word a person reads is not the word the
 * model returns, and only the first one changes here.
 *
 * `warn` becomes REVIEW rather than a shorter synonym for "warning". That is
 * the accurate name for what it does: `DEFAULT_POLICY.autocontinue_on_warn` is
 * `false`, so a `warn` **stops the run and waits for a person**. Observed on
 * run `20260822T023010Z-28801d6c`, where `fastq_qc` returned `warn` and the run
 * halted with exit code 2. A label reading "warning" describes the severity and
 * says nothing about the thing that just happened to the run.
 *
 * RUNNING is here too, and is deliberately **not** a fifth alarm colour — see
 * `stepTone`.
 */
export const TONE_WORDS: Record<Tone, string> = {
  pass: "PASS",
  warn: "REVIEW",
  fail: "STOP",
  reused: "REUSED",
  running: "RUNNING",
  muted: "—",
};

/**
 * A step's tone, given its recorded status and judge verdict.
 *
 * ## RUNNING is a state, not a severity
 *
 * A step in flight used to fall through to `muted`, which is also what an
 * unrecognised status renders as — so "working on it" and "nothing is known
 * about this" were one colour and one word. It stays neutral, because colour
 * here encodes *does somebody need to act*, and a fifth alarm colour would
 * dilute the amber that means REVIEW. What makes it legible instead is the
 * word, and an elapsed clock beside it: a static RUNNING sitting for
 * twenty-five minutes is indistinguishable from a hung process, which is
 * exactly how a memory-throttled `cellranger_count` read on
 * 2026-08-22 before its `--localmem` budget was understood.
 */
export function stepTone(status: string, verdict: string | null | undefined): Tone {
  // A reused step did not run in this execution. Note that its verdict may
  // still have been produced *this* time: a resume re-judges what it reuses,
  // and the same payload has returned `pass` on one pass and `warn` on the
  // next. `reused` is the honest label for the artifact either way.
  if (status === "skipped") return "reused";
  if (status === "error") return "fail";
  if (status === "running") return "running";
  if (verdict === "pass") return "pass";
  if (verdict === "warn") return "warn";
  if (verdict === "fail") return "fail";
  return "muted";
}

/** The word on the badge: PASS / REVIEW / STOP / REUSED / RUNNING. */
export function stepToneLabel(status: string, verdict: string | null | undefined): string {
  const tone = stepTone(status, verdict);
  // An unrecognised status is shown as itself rather than as an em dash. The
  // gateway is a separate deploy, and a status this build has never heard of
  // is information — hiding it behind a placeholder is not.
  if (tone === "muted") return status || "unknown";
  return TONE_WORDS[tone];
}

/**
 * A whole run's tone, from the status the gateway derived.
 *
 * Three of these want a person to do something and one does not, which is the
 * distinction the colour is carrying:
 *
 * - `needs_review` waits for a decision. It used to arrive as `halted`, the
 *   executor's word for a run somebody *stopped*, so "waiting for you" and
 *   "ended by you" were one colour and one word.
 * - `interrupted` is a run whose process is gone mid-step. It is a failure of
 *   the machinery rather than of the science, so it is warned about rather
 *   than marked failed — the work up to that step is still on disk and
 *   `--resume-from` can pick it up.
 * - `running` is neutral on purpose: nothing is being asked of anybody. It is
 *   its own tone rather than `muted` so that the page can say RUNNING and put
 *   an elapsed time beside it, which is the only thing that distinguishes
 *   working from hung.
 */
export function runTone(status: string): Tone {
  if (status === "completed") return "pass";
  if (status === "needs_review") return "warn";
  if (status === "interrupted") return "warn";
  if (status === "halted") return "warn";
  if (status === "failed") return "fail";
  if (status === "running") return "running";
  return "muted";
}

/** A run's status as a word, using the same vocabulary as the step badges. */
export function runToneLabel(status: string): string {
  const tone = runTone(status);
  if (tone === "muted") return status || "unknown";
  return TONE_WORDS[tone];
}

/** `2159` -> `2,159`; `null` -> null, so a caller must decide what absence looks like. */
export function formatCount(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  return value.toLocaleString("en-US");
}

/** An ISO timestamp as something readable, or the raw string if it will not parse. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z").replace(/Z$/, " UTC");
}

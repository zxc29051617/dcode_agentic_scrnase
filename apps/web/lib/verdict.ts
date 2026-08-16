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

export type Tone = "pass" | "warn" | "fail" | "reused" | "muted";

/** A step's tone, given its recorded status and judge verdict. */
export function stepTone(status: string, verdict: string | null | undefined): Tone {
  // A reused step did not run in this execution, so whatever verdict is
  // recorded belongs to the run that produced it. Saying `reused` is the
  // honest label; showing it as a fresh `pass` is not.
  if (status === "skipped") return "reused";
  if (status === "error") return "fail";
  if (verdict === "pass") return "pass";
  if (verdict === "warn") return "warn";
  if (verdict === "fail") return "fail";
  return "muted";
}

export function stepToneLabel(status: string, verdict: string | null | undefined): string {
  const tone = stepTone(status, verdict);
  if (tone === "reused") return "reused";
  if (tone === "muted") return status || "unknown";
  return verdict ?? tone;
}

/** A whole run's tone, from the status the gateway derived. */
export function runTone(status: string): Tone {
  if (status === "completed") return "pass";
  if (status === "halted") return "warn";
  if (status === "failed") return "fail";
  if (status === "running") return "muted";
  return "muted";
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

/**
 * Durations in words, at the resolution the reader can act on.
 *
 * Nobody watching a run needs `1847.3 s`. What they need is whether to keep
 * waiting, and that is a question about minutes. Under a minute stays in
 * seconds because at that scale "less than a minute" is the useful fact and
 * rounding it to zero minutes reads as "instant".
 */
export function humanDuration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 1) return "under a second";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes < 10 ? minutes.toFixed(1) : Math.round(minutes)} min`;
  const hours = minutes / 60;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h`;
}

/**
 * A measured expectation, or nothing.
 *
 * Returns null rather than a guess when the gateway has too few finished runs
 * to draw one from. A range is included whenever the samples disagree by more
 * than a quarter, because "about 30 minutes" from runs of 12 and 48 is a
 * different thing to know than the same words from runs of 29 and 31 — and
 * the reader is using it to decide whether being at 40 minutes is normal.
 */
export function expectedDuration(
  timing: { n: number; median_seconds: number; min_seconds: number; max_seconds: number } | undefined,
): string | null {
  if (!timing) return null;
  const median = humanDuration(timing.median_seconds);
  if (!median) return null;
  const spread = timing.max_seconds - timing.min_seconds;
  if (spread > timing.median_seconds * 0.25) {
    const lo = humanDuration(timing.min_seconds);
    const hi = humanDuration(timing.max_seconds);
    return `${median} typically (${lo}–${hi} across ${timing.n} runs)`;
  }
  return `${median} typically (${timing.n} runs)`;
}

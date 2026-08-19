import { NextResponse } from "next/server";
import { getRunSnapshot } from "@/lib/gateway";

/**
 * The few fields a running run's progress panel refreshes on.
 *
 * A Route Handler rather than a direct fetch because `lib/gateway.ts` is
 * `server-only` and the browser must never learn `GATEWAY_URL` — the same rule
 * every other read on this site follows. What crosses back is deliberately
 * four fields and not the snapshot: the panel needs no more, and a polling
 * endpoint that returns everything becomes the shape people build against.
 */
export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const snapshot = await getRunSnapshot(runId);
  if (!snapshot) {
    return NextResponse.json({ error: "no such run" }, { status: 404 });
  }
  return NextResponse.json(
    {
      status: snapshot.status,
      unfinished_step: snapshot.unfinished_step,
      current_step_elapsed_seconds: snapshot.current_step_elapsed_seconds,
      steps: snapshot.steps.map((s) => ({ step: s.step, status: s.status })),
    },
    { headers: { "cache-control": "no-store" } },
  );
}

import { NextRequest, NextResponse } from "next/server";
import { ControllerError, confirmAnalysisRequest, controllerConfigured } from "@/lib/controller";
import { resolveOperator } from "@/lib/operator";

/**
 * The one route in this app that can cause a scientific run to exist.
 *
 * Three things make that safe, and all three are here rather than in a prompt:
 *
 * **The operator identity comes from the server.** `resolveOperator()` reads
 * the environment; the request body's opinion of who is confirming is ignored
 * entirely. A browser cannot claim to be somebody and a model has no route to
 * this handler at all — there is no CopilotKit action that reaches it, by
 * design (`lib/intakeActions.ts`).
 *
 * **The digest comes from the draft the person was looking at.** It is passed
 * through to the controller, which refuses a mismatch. A draft edited in
 * another tab between render and click cannot be confirmed under the version
 * that was read.
 *
 * **A second POST is not a second run.** The controller's store has a unique
 * index on one start job per request, so a double-click, a retried fetch and
 * two tabs all converge on the same job. This route does not need to guard
 * that; it needs to not defeat it, which it does by passing the request id
 * straight through rather than minting anything.
 */

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ requestId: string }> },
) {
  if (!controllerConfigured()) {
    return NextResponse.json(
      { error: "controller_not_configured",
        detail: "ANALYSIS_CONTROLLER_URL is not set, so no analysis can be started." },
      { status: 503 },
    );
  }

  const operator = resolveOperator();
  if (!operator.ok) {
    return NextResponse.json({ error: "no_operator_identity", detail: operator.reason },
      { status: 500 });
  }

  const { requestId } = await params;
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const digest = typeof body.config_digest === "string" ? body.config_digest : "";
  if (!digest) {
    return NextResponse.json(
      { error: "missing_config_digest",
        detail: "A confirmation must name the version of the request it is confirming." },
      { status: 400 },
    );
  }
  const rationale = typeof body.rationale === "string" ? body.rationale : undefined;

  try {
    const result = await confirmAnalysisRequest(requestId, {
      config_digest: digest,
      // Never `body.operator_id`. The client does not get to say who this is.
      operator_id: operator.operatorId,
      rationale,
    });
    return NextResponse.json({ ...result, operator_mode: operator.mode });
  } catch (error) {
    if (error instanceof ControllerError) {
      return NextResponse.json({ error: "controller_error", detail: error.detail },
        { status: error.status });
    }
    console.error("[analysis-requests/confirm]", error);
    return NextResponse.json({ error: "confirm_failed" }, { status: 500 });
  }
}

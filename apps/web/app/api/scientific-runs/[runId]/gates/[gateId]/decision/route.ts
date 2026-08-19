import { NextRequest, NextResponse } from "next/server";
import { ControllerError, controllerConfigured, submitGateDecision } from "@/lib/controller";
import { resolveOperator } from "@/lib/operator";

/**
 * One human answer to one pending gate, forwarded to the controller.
 *
 * What this route does *not* do is the point. It does not convert an override
 * value, does not decide whether a parameter may be set at this gate, and does
 * not check whether the decision is stale. All three are the controller's,
 * through `src/registry.py::coerce_overrides` and the pending gate's own
 * recorded generation — one semantic validator, shared with the terminal, so a
 * value typed in a browser and a value typed at a prompt take the same path
 * through the same code.
 *
 * A client-side conversion here would be a second opinion about what
 * `min_genes = "250"` means, and the first time the two disagreed the browser
 * would be the one nobody audits.
 *
 * `expected_generation` is passed through from the page that rendered the
 * gate. That is what makes a decision refer to the question that was actually
 * shown: if another operator answered in the meantime, the controller refuses
 * rather than applying this answer to whatever the run is waiting on now.
 */

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ runId: string; gateId: string }> },
) {
  if (!controllerConfigured()) {
    return NextResponse.json(
      { error: "controller_not_configured",
        detail: "ANALYSIS_CONTROLLER_URL is not set, so gates must be answered at the terminal." },
      { status: 503 },
    );
  }

  const operator = resolveOperator();
  if (!operator.ok) {
    return NextResponse.json({ error: "no_operator_identity", detail: operator.reason },
      { status: 500 });
  }

  const { runId, gateId } = await params;
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const decision = body.decision;
  if (decision !== "accept" && decision !== "revise" && decision !== "stop") {
    return NextResponse.json(
      { error: "invalid_decision", detail: "decision must be accept, revise or stop" },
      { status: 400 },
    );
  }

  const generation = Number(body.expected_generation);
  if (!Number.isInteger(generation) || generation < 1) {
    return NextResponse.json(
      { error: "invalid_generation",
        detail: "expected_generation must name the gate this decision was made against" },
      { status: 400 },
    );
  }

  const overrides =
    body.overrides && typeof body.overrides === "object" && !Array.isArray(body.overrides)
      ? (body.overrides as Record<string, unknown>)
      : {};

  try {
    const result = await submitGateDecision(runId, gateId, {
      decision,
      operator_id: operator.operatorId,
      expected_generation: generation,
      rationale: typeof body.rationale === "string" ? body.rationale : undefined,
      overrides,
    });
    return NextResponse.json({ ...result, operator_mode: operator.mode });
  } catch (error) {
    if (error instanceof ControllerError) {
      return NextResponse.json({ error: "controller_error", detail: error.detail },
        { status: error.status });
    }
    console.error("[gates/decision]", error);
    return NextResponse.json({ error: "decision_failed" }, { status: 500 });
  }
}

import { NextRequest, NextResponse } from "next/server";
import { ControllerError, previewAnalysisRequest, controllerConfigured } from "@/lib/controller";

/**
 * Server-side proxy for the controller's preview endpoint.
 *
 * The browser posts here, not to the controller: `ANALYSIS_CONTROLLER_URL` is
 * read inside `lib/controller.ts`, which is `server-only`, so the address never
 * reaches client JavaScript and the controller never needs to be exposed to a
 * network the browser is on.
 *
 * The body is filtered to the fields the controller's own schema names. That
 * is not a substitute for the controller's validation — which is the real one,
 * and which this cannot weaken — but it keeps this route from being a general
 * forwarder for whatever a page decides to send.
 */

const ALLOWED_FIELDS = [
  "request_id",
  "conversation_id",
  "project",
  "species",
  "research_question",
  "input_ref",
  "input_path",
  "input_kind_hint",
  "study_design_ref",
  "analysis",
] as const;

export async function POST(request: NextRequest) {
  if (!controllerConfigured()) {
    return NextResponse.json(
      {
        error: "controller_not_configured",
        detail:
          "ANALYSIS_CONTROLLER_URL is not set, so this deployment cannot prepare an analysis " +
          "request. The read-only pages work without it.",
      },
      { status: 503 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const filtered: Record<string, unknown> = {};
  for (const field of ALLOWED_FIELDS) {
    if (body[field] !== undefined) filtered[field] = body[field];
  }

  try {
    return NextResponse.json(await previewAnalysisRequest(filtered));
  } catch (error) {
    if (error instanceof ControllerError) {
      return NextResponse.json({ error: "controller_error", detail: error.detail },
        { status: error.status });
    }
    // Anything else is a bug here, not a message for a browser: the text could
    // quote an internal address or a stack.
    console.error("[analysis-requests/preview]", error);
    return NextResponse.json({ error: "preview_failed" }, { status: 500 });
  }
}

import { NextRequest, NextResponse } from "next/server";
import { ControllerError, controllerConfigured, getAnalysisRequestStatus } from "@/lib/controller";

/**
 * Server-side proxy for one request's status. Read-only, and what the intake
 * page polls while a run is queued or running.
 */

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ requestId: string }> },
) {
  if (!controllerConfigured()) {
    return NextResponse.json({ error: "controller_not_configured" }, { status: 503 });
  }
  const { requestId } = await params;
  try {
    return NextResponse.json(await getAnalysisRequestStatus(requestId));
  } catch (error) {
    if (error instanceof ControllerError) {
      return NextResponse.json({ error: "controller_error", detail: error.detail },
        { status: error.status });
    }
    console.error("[analysis-requests/status]", error);
    return NextResponse.json({ error: "status_failed" }, { status: 500 });
  }
}

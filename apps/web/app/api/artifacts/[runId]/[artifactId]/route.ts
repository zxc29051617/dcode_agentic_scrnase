import { NextRequest, NextResponse } from "next/server";
import { artifactUrl } from "@/lib/gateway";

/**
 * Streams one artifact from the gateway to the browser.
 *
 * This exists because the browser must never learn `GATEWAY_URL`. An `<img>`
 * or an `<iframe>` pointing straight at the gateway would put its address in
 * the page for anyone to read, and would only work at all when the browser
 * happens to sit on the same host. Every byte a page displays therefore comes
 * through this same-origin route.
 *
 * It adds nothing to what the gateway allows and takes nothing away: the run
 * id and artifact id are passed through as opaque strings, and an id the
 * gateway's manifest did not produce still resolves to nothing. What it does
 * add is a second copy of the isolation headers, so a report is sandboxed
 * whether it is fetched from here or from the gateway directly.
 */

/** Only these ever reach the browser; everything else the gateway sends is dropped. */
const FORWARDED_HEADERS = ["content-type", "content-length", "content-disposition"];

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ runId: string; artifactId: string }> },
) {
  const { runId, artifactId } = await params;
  const download = request.nextUrl.searchParams.get("download") === "1";

  let upstream: Response;
  try {
    upstream = await fetch(artifactUrl(runId, artifactId, download), { cache: "no-store" });
  } catch (error) {
    // The gateway's own message names its URL; that must not travel to a
    // browser, so this reports the failure without it.
    console.error("[artifacts] gateway unreachable:", error);
    return NextResponse.json(
      { error: "artifact_unavailable", message: "the run gateway could not be reached" },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    return NextResponse.json(
      {
        error: "artifact_unavailable",
        // 404 and 413 are the two the gateway raises deliberately; both are
        // safe to relay as a status, and neither body is passed through.
        message:
          upstream.status === 413
            ? "this artifact is larger than the gateway will serve"
            : "no such artifact in this run",
      },
      { status: upstream.status === 413 ? 413 : 404 },
    );
  }

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  // Restated here rather than forwarded, so the isolation does not depend on
  // the upstream having set it. `sandbox` without `allow-same-origin` gives
  // the document an opaque origin: its scripts can run, and can touch nothing
  // of this app's.
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Content-Security-Policy", "sandbox allow-scripts; frame-ancestors 'self'");
  headers.set("Cache-Control", "private, max-age=60");

  return new NextResponse(upstream.body, { status: 200, headers });
}

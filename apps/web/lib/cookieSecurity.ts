import "server-only";
import type { NextRequest } from "next/server";

/**
 * Whether the request that reached this server was made over HTTPS.
 *
 * Checked rather than assumed, because a session cookie's `Secure` flag has
 * to be conditional on this in a way a constant cannot be: set `Secure` on a
 * plain-HTTP deployment and the browser refuses to send the cookie back at
 * all, silently breaking every session; leave `Secure` off on an HTTPS
 * deployment and the cookie can be read by anyone who can sniff the
 * network — which matters more here than most places, since this cookie is
 * what stands between one visitor's session and another's on a shared lab
 * network.
 *
 * `x-forwarded-proto` is trusted first because a reverse proxy terminating
 * TLS in front of this app (the normal way to add HTTPS to a `next start`
 * deployment) makes the direct connection to Node look like plain HTTP even
 * though the browser used HTTPS to reach the proxy.
 */
export function isRequestSecure(request: NextRequest): boolean {
  const forwarded = request.headers.get("x-forwarded-proto");
  if (forwarded) {
    return forwarded.split(",")[0]!.trim().toLowerCase() === "https";
  }
  return request.nextUrl.protocol === "https:";
}

/**
 * Cookie attributes for anything that must not be readable by page
 * JavaScript and must not follow a cross-site request.
 *
 * `httpOnly` is the primary defence here — it is what keeps a script on the
 * page (including any of the third-party JS this app loads, e.g.
 * `@copilotkit/react-ui`) from ever reading the session id, regardless of
 * transport. `sameSite: "strict"` means a form or fetch from another site
 * cannot ride this cookie in either direction. `secure` is conditional per
 * `isRequestSecure` rather than hardcoded — see that function's docstring.
 */
export function secureSessionCookieOptions(request: NextRequest, maxAgeMs: number) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    secure: isRequestSecure(request),
    path: "/",
    maxAge: Math.floor(maxAgeMs / 1000),
  };
}

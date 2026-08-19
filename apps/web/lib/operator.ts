import "server-only";

/**
 * Who a confirmation or a gate decision is recorded as.
 *
 * The rule this file exists to enforce: **the identity is decided on the
 * server**. It is not a field in the request body, so a browser cannot claim
 * to be somebody, and it is not reachable by the model at all, so an assistant
 * cannot supply one either. `docs/copilotkit_product_architecture.md` §1.4:
 * "The server, not the browser or an agent, records the operator identity."
 *
 * ## Local development, honestly labelled
 *
 * There is no authentication in this slice. `ANALYSIS_OPERATOR_ID` names the
 * one person using a local stack, and that is a real limitation rather than a
 * placeholder to be forgotten: it identifies whoever is running the server, not
 * whoever is at the browser, and two people sharing a local stack would be
 * recorded as one.
 *
 * What it must never do is default to anonymous. A run whose gates were
 * answered by "someone" is a run whose decisions cannot be attributed, and the
 * audit log is one of the two things this project's provenance rests on. So an
 * unset variable in production mode is a refusal, not a fallback.
 */

export type OperatorIdentity =
  | { ok: true; operatorId: string; mode: "local" | "configured" }
  | { ok: false; reason: string };

const LOCAL_DEFAULT = "local-operator";

export function resolveOperator(env: NodeJS.ProcessEnv = process.env): OperatorIdentity {
  const configured = (env.ANALYSIS_OPERATOR_ID ?? "").trim();
  if (configured) {
    return { ok: true, operatorId: configured, mode: "configured" };
  }
  // `next dev` and an explicit opt-in are the only ways to get the placeholder.
  const isLocal = env.NODE_ENV !== "production" || env.ANALYSIS_ALLOW_LOCAL_OPERATOR === "true";
  if (isLocal) {
    return { ok: true, operatorId: LOCAL_DEFAULT, mode: "local" };
  }
  return {
    ok: false,
    reason:
      "ANALYSIS_OPERATOR_ID is not set. A confirmation and a gate decision are attributed to a " +
      "person; this deployment has no way to name one, so it refuses rather than recording an " +
      "anonymous decision.",
  };
}

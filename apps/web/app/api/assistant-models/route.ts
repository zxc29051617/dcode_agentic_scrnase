import { NextResponse } from "next/server";
import { getAssistantModelConfig, scrubSecrets } from "@/lib/assistantModel";

/**
 * Which models the configured local endpoint currently serves.
 *
 * No new credential is introduced by this route: it asks the same
 * `ASSISTANT_MODEL_BASE_URL` / key already configured server-side for
 * `/api/copilotkit`, over the OpenAI-compatible `/models` list every
 * Ollama/vLLM-style endpoint implements. This is why a model *selector* is a
 * much smaller decision than a *bring-your-own-key* one: it only picks a
 * name to send to an endpoint this server already trusts, and reveals
 * nothing about it that a visitor with access to this page could not already
 * infer from the assistant answering at all.
 */

const LIST_TIMEOUT_MS = 5_000;

export async function GET() {
  const config = getAssistantModelConfig();
  if (!config.configured) {
    return NextResponse.json({ models: [], defaultModel: null, reason: config.reason });
  }

  try {
    const res = await fetch(`${config.baseURL.replace(/\/$/, "")}/models`, {
      headers: { Authorization: `Bearer ${config.apiKey}` },
      signal: AbortSignal.timeout(LIST_TIMEOUT_MS),
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`endpoint returned ${res.status}`);
    }
    const data = (await res.json()) as { data?: { id?: unknown }[] };
    const models = Array.isArray(data.data)
      ? data.data.map((m) => (typeof m.id === "string" ? m.id : null)).filter((id): id is string => id !== null)
      : [];
    // The configured default is always offered even if the endpoint's own
    // listing omits it for some reason — a dropdown that cannot select the
    // thing already running would be strictly worse than the fixed value it
    // replaces.
    if (!models.includes(config.model)) models.unshift(config.model);
    return NextResponse.json({ models, defaultModel: config.model });
  } catch (error) {
    // Falls back to a one-item list rather than an empty dropdown: the
    // endpoint being briefly unreachable for listing should not take away
    // the ability to use the model that is already configured.
    // `fetch` failure messages can quote the URL that failed; scrub it the
    // same way lib/assistantModel.ts scrubs an OpenAI client error, so an
    // internal endpoint address never leaves this route in an error string.
    const raw = error instanceof Error ? error.message : String(error);
    return NextResponse.json({
      models: [config.model],
      defaultModel: config.model,
      warning: `could not list models from the endpoint: ${scrubSecrets(raw, [config.baseURL, config.apiKey])}`,
    });
  }
}

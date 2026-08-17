import "server-only";

/**
 * Assistant model configuration, read from the server environment only.
 *
 * Three variables, all server-side, none carrying the `NEXT_PUBLIC_` prefix
 * that is the one thing in Next.js which inlines a value into client-side
 * JavaScript:
 *
 *     ASSISTANT_MODEL_BASE_URL   an OpenAI-compatible endpoint, ending in /v1
 *     ASSISTANT_MODEL_NAME       which model to ask
 *     ASSISTANT_MODEL_API_KEY    optional; Ollama ignores it, OpenAI requires it
 *
 * The API key is read in exactly one place — `parseAssistantModelConfig` —
 * and leaves this module only inside the OpenAI client that needs it. It is
 * never returned to a caller, never put in a status object, never logged, and
 * never rendered. `describeAssistantModel()` exists so the UI can say what it
 * is talking to without this module having to hand out the credential to do
 * it.
 *
 * `ASSISTANT_MODEL_NAME` is deliberately required rather than defaulted. This
 * project already learned the shape of that mistake in `annotate_cells`: a
 * model chosen for you is a confident wrong answer waiting to happen, so the
 * pipeline lists the candidates and stops instead of guessing. An assistant
 * silently answering from whatever model an endpoint happens to serve first
 * is the same failure with a friendlier face.
 */

export type AssistantModelConfig =
  | {
      configured: true;
      baseURL: string;
      apiKey: string;
      model: string;
      /** The endpoint with any embedded credentials removed. Safe to display. */
      displayEndpoint: string;
    }
  | { configured: false; reason: string };

/** What a browser is allowed to know. Never carries the key. */
export type AssistantModelStatus =
  | { configured: true; model: string; endpoint: string }
  | { configured: false; reason: string };

/**
 * The endpoint with any `user:password@` removed.
 *
 * The URL form allows credentials inside it, and this string is rendered in a
 * browser and may end up in a screenshot or a bug report. `src/judge.py`
 * strips the same thing for the same reason before writing an endpoint into
 * `run_metadata.json`; this is that rule applied to the other surface that
 * displays one.
 */
export function sanitizeEndpoint(raw: string): string {
  try {
    const url = new URL(raw);
    url.username = "";
    url.password = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "<unparseable endpoint>";
  }
}

/**
 * Remove anything secret from a string before it is logged or returned.
 *
 * Called on every error message that leaves this module. An OpenAI client
 * error can quote the request it failed on, and a misconfigured endpoint can
 * carry credentials in its own URL — neither is a reason to hand either to a
 * browser.
 */
export function scrubSecrets(text: string, secrets: (string | undefined)[]): string {
  let out = text;
  for (const secret of secrets) {
    if (secret && secret.length >= 4) {
      out = out.split(secret).join("[redacted]");
    }
  }
  return out;
}

/**
 * Parse the three variables. Pure: takes the environment rather than reading
 * `process.env`, so the rules can be tested without mutating global state.
 */
export function parseAssistantModelConfig(
  env: Record<string, string | undefined>,
): AssistantModelConfig {
  const baseURL = env.ASSISTANT_MODEL_BASE_URL?.trim();
  const model = env.ASSISTANT_MODEL_NAME?.trim();

  if (!baseURL) {
    return {
      configured: false,
      reason: "ASSISTANT_MODEL_BASE_URL is not set",
    };
  }
  if (!model) {
    return {
      configured: false,
      reason:
        "ASSISTANT_MODEL_NAME is not set (naming the model is required, not defaulted)",
    };
  }

  let parsed: URL;
  try {
    parsed = new URL(baseURL);
  } catch {
    return {
      configured: false,
      // Deliberately does not echo the value back: a malformed endpoint is
      // exactly the case where someone has pasted a credential into it.
      reason: "ASSISTANT_MODEL_BASE_URL is not a valid URL",
    };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return {
      configured: false,
      reason: "ASSISTANT_MODEL_BASE_URL must be http or https",
    };
  }

  return {
    configured: true,
    baseURL,
    // Ollama and vLLM ignore the key but the OpenAI client insists on one
    // being present — the same accommodation `.env.example` makes for the
    // judge with `SCRNA_JUDGE_API_KEY=not-needed`.
    apiKey: env.ASSISTANT_MODEL_API_KEY?.trim() || "not-needed",
    model,
    displayEndpoint: sanitizeEndpoint(baseURL),
  };
}

/** The current configuration, from the live server environment. */
export function getAssistantModelConfig(): AssistantModelConfig {
  return parseAssistantModelConfig(process.env);
}

/**
 * What the assistant page may render. Derived from the config with the key
 * dropped — this is the only shape that crosses into a React tree.
 */
export function describeAssistantModel(): AssistantModelStatus {
  const config = getAssistantModelConfig();
  if (!config.configured) {
    return { configured: false, reason: config.reason };
  }
  return {
    configured: true,
    model: config.model,
    endpoint: config.displayEndpoint,
  };
}

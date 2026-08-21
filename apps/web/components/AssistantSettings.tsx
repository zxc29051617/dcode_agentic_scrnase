"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { AssistantModelsResponse, AssistantSessionStatus } from "@/lib/assistantSessionTypes";

/**
 * Picks which model answers in this browser's session — a local model from
 * the lab endpoint, or the visitor's own OpenAI key.
 *
 * Nothing here ever holds a typed-in key past the moment it is POSTed:
 * `apiKeyInput` is React state that lives only in this component's memory
 * and is cleared the instant the request that used it returns, whether it
 * succeeded or not. The key is never read back from the server afterward —
 * `GET /api/assistant-session` only ever returns whether one is set, never
 * its value — so there is nothing for this component to display beyond
 * that boolean.
 *
 * ## Why saving refreshes the server render
 *
 * The header names the model that would answer, and it is resolved in a Server
 * Component — the session store is server-side memory keyed by an `httpOnly`
 * cookie, which page JavaScript cannot read by design. Saving here is a
 * browser-side POST, so the HTML carrying that header has already been sent
 * and nothing re-runs on its own: this panel would say "using your OpenAI key"
 * while the header a few pixels above went on naming the lab default, and the
 * two would disagree until somebody happened to reload.
 *
 * `router.refresh()` re-runs the Server Components for the current route
 * without a full navigation, so the header catches up in the same interaction
 * that changed it. Client state here is preserved across it, so nothing typed
 * is lost.
 */
export default function AssistantSettings({ onChanged }: { onChanged?: () => void }) {
  const router = useRouter();
  const [status, setStatus] = useState<AssistantSessionStatus>({ active: false });
  const [localModels, setLocalModels] = useState<AssistantModelsResponse | null>(null);
  const [provider, setProvider] = useState<"local" | "openai">("local");
  const [selectedLocalModel, setSelectedLocalModel] = useState("");
  const [openaiModel, setOpenaiModel] = useState("gpt-4o-mini");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetch("/api/assistant-session")
      .then((r) => r.json())
      .then((s: AssistantSessionStatus) => {
        setStatus(s);
        if (s.active) {
          setProvider(s.provider);
          if (s.provider === "local") setSelectedLocalModel(s.model);
          else setOpenaiModel(s.model);
        }
      })
      .catch(() => {});
    fetch("/api/assistant-models")
      .then((r) => r.json())
      .then((m: AssistantModelsResponse) => {
        setLocalModels(m);
        setSelectedLocalModel((current) => current || m.defaultModel || m.models[0] || "");
      })
      .catch(() => {});
  }, []);

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const body =
        provider === "local"
          ? { provider: "local", model: selectedLocalModel }
          : { provider: "openai", model: openaiModel, apiKey: apiKeyInput };
      const res = await fetch("/api/assistant-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json()) as AssistantSessionStatus | { error: string; message: string };
      if (!res.ok) {
        setMessage("error" in data ? data.message : "could not save");
        return;
      }
      // Cleared immediately after the request completes — nothing keeps the
      // typed key in memory once the server has acknowledged it.
      setApiKeyInput("");
      setStatus(data as AssistantSessionStatus);
      setMessage("saved");
      onChanged?.();
      router.refresh();
    } catch {
      setMessage("could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/assistant-session", { method: "DELETE" });
      const data = (await res.json()) as AssistantSessionStatus | { error?: string; message?: string };
      if (!res.ok) {
        setMessage("message" in data ? data.message ?? "could not clear" : "could not clear");
        return;
      }
      setStatus(data as AssistantSessionStatus);
      setApiKeyInput("");
      setProvider("local");
      setMessage("cleared");
      onChanged?.();
      router.refresh();
    } catch {
      setMessage("could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" style={{ margin: "0.6rem 0.9rem" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ width: "100%", textAlign: "left", background: "none", border: "none", padding: 0 }}
      >
        <strong>Assistant settings</strong>{" "}
        <span className="subtle">
          {status.active
            ? status.provider === "openai"
              ? `— using your OpenAI key (${status.model})`
              : `— using ${status.model}`
            : "— using the lab default"}
        </span>
      </button>

      {open && (
        <div style={{ marginTop: "0.7rem" }}>
          <div className="controls" style={{ gap: "1rem" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
              <input
                type="radio"
                name="assistant-provider"
                checked={provider === "local"}
                onChange={() => setProvider("local")}
              />
              Local model (lab endpoint)
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
              <input
                type="radio"
                name="assistant-provider"
                checked={provider === "openai"}
                onChange={() => setProvider("openai")}
              />
              My own OpenAI key
            </label>
          </div>

          {provider === "local" ? (
            <div style={{ marginTop: "0.5rem" }}>
              <select
                value={selectedLocalModel}
                onChange={(e) => setSelectedLocalModel(e.target.value)}
                aria-label="Local model"
              >
                {(localModels?.models ?? []).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              {localModels?.warning && (
                <p className="subtle" style={{ margin: "0.3rem 0 0" }}>
                  {localModels.warning}
                </p>
              )}
            </div>
          ) : (
            <div style={{ marginTop: "0.5rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
              <p className="subtle" style={{ margin: 0 }}>
                Sent once to this server, held only for this browser session, never written to
                disk, and cleared if the server restarts. Only you can use it — it is not shared
                with anyone else viewing this site.
              </p>
              <input
                type="text"
                placeholder="gpt-4o-mini"
                value={openaiModel}
                onChange={(e) => setOpenaiModel(e.target.value)}
                aria-label="OpenAI model"
              />
              <input
                type="password"
                placeholder="sk-..."
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                autoComplete="off"
                aria-label="OpenAI API key"
              />
              {status.active && status.provider === "openai" && (
                // There is nothing to "keep": the server never returns a key
                // once set, and this component never caches one past the
                // request that sent it — so changing anything else about
                // this session (even just the model name) needs the key
                // typed again. That is a direct consequence of the key
                // truly not being retrievable, not a UI limitation to work
                // around.
                <p className="subtle" style={{ margin: 0 }}>
                  A key is set for this session, but it cannot be recalled or shown here — type it
                  again to change the model or replace it.
                </p>
              )}
            </div>
          )}

          <div className="controls" style={{ marginTop: "0.6rem" }}>
            <button
              onClick={save}
              // Always required for "openai": there is no stored value this
              // save could fall back to keeping. See the note above the key
              // input for why.
              disabled={busy || (provider === "openai" && !apiKeyInput)}
              data-variant="primary"
            >
              Save
            </button>
            <button onClick={clear} disabled={busy || !status.active}>
              Clear
            </button>
            {message && <span className="subtle">{message}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

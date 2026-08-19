"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import type {
  DatasetOption,
  PreviewResponse,
  RequestStatusView,
  StudyDesignOption,
} from "@/lib/controllerTypes";

/**
 * The intake page's interactive half: a conversation on one side, and the
 * server's structured draft on the other.
 *
 * ## The draft card never parses the assistant
 *
 * Everything rendered below comes from `PreviewResponse`, which is JSON the
 * controller returned. Nothing here reads the assistant's prose, and nothing
 * here decides whether the request is confirmable — `can_confirm` is the
 * server's answer and the button follows it. That matters because the
 * assistant is a language model: it can describe a draft it did not create, or
 * describe one it did create inaccurately, and a card built from its words
 * would show a request that does not exist.
 *
 * The card is refreshed by polling the request, not by watching the chat. When
 * the assistant calls `prepare_analysis_request`, the controller has the new
 * draft before the assistant has finished its sentence, so the card leads the
 * conversation rather than trailing it.
 */

const POLL_MS = 2_000;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

type Draft = PreviewResponse | null;

function Field({ label, value, missing }: { label: string; value: React.ReactNode; missing?: boolean }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "11rem 1fr", gap: "0.5rem", padding: "0.2rem 0" }}>
      <span className="subtle">{label}</span>
      <span className={missing ? "subtle" : undefined}>{missing ? "not given yet" : value}</span>
    </div>
  );
}

function AnalysisList({ analysis }: { analysis: Record<string, unknown> }) {
  const entries = Object.entries(analysis);
  if (entries.length === 0) {
    return <span className="subtle">defaults — the run will stop at a gate for anything it needs</span>;
  }
  return (
    <span>
      {entries.map(([key, value]) => (
        <code key={key} style={{ marginRight: "0.6rem" }}>
          {key}={JSON.stringify(value)}
        </code>
      ))}
    </span>
  );
}

export default function AnalysisIntake({
  datasets,
  studyDesigns,
  instructions,
  operatorMode,
  modelConfigured,
  modelReason,
}: {
  datasets: DatasetOption[];
  studyDesigns: StudyDesignOption[];
  instructions: string;
  operatorMode: "local" | "configured" | "unavailable";
  modelConfigured: boolean;
  modelReason: string | null;
}) {
  const [draft, setDraft] = useState<Draft>(null);
  const [status, setStatus] = useState<RequestStatusView | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const requestIdRef = useRef<string | null>(null);
  const startedAtRef = useRef<number>(0);

  // --- keep the card in step with the server -------------------------------
  // The assistant creates and revises drafts through the controller; this
  // watches for one appearing under this conversation and then follows it.
  const conversationId = useRef<string>(
    `copilot-${Math.random().toString(36).slice(2, 10)}`,
  ).current;

  const refresh = useCallback(async (requestId: string) => {
    const response = await fetch(`/api/analysis-requests/${encodeURIComponent(requestId)}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`status ${response.status}`);
    return (await response.json()) as RequestStatusView;
  }, []);

  const previewFromForm = useCallback(
    async (body: Record<string, unknown>) => {
      setError(null);
      const response = await fetch("/api/analysis-requests/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...body, conversation_id: conversationId }),
      });
      const parsed = await response.json();
      if (!response.ok) {
        setError(typeof parsed.detail === "string" ? parsed.detail : "preview failed");
        return;
      }
      const preview = parsed as PreviewResponse;
      requestIdRef.current = preview.request.request_id;
      setDraft(preview);
    },
    [conversationId],
  );

  // Polling, with a timeout and a visible error state rather than a spinner
  // that never resolves.
  useEffect(() => {
    const id = requestIdRef.current ?? draft?.request.request_id ?? null;
    if (!id) return;
    const live = status?.status ?? draft?.request.status;
    if (live && ["completed", "failed", "cancelled", "rejected"].includes(live)) return;
    if (!startedAtRef.current) startedAtRef.current = Date.now();

    const timer = setInterval(async () => {
      if (Date.now() - startedAtRef.current > POLL_TIMEOUT_MS) {
        setPollError("stopped watching after 15 minutes — reload to check again");
        clearInterval(timer);
        return;
      }
      try {
        setStatus(await refresh(id));
        setPollError(null);
      } catch (err) {
        setPollError(err instanceof Error ? err.message : "could not reach the server");
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [draft, status, refresh]);

  const confirm = useCallback(async () => {
    const request = draft?.request;
    if (!request?.config_digest || confirming) return;
    setConfirming(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/analysis-requests/${encodeURIComponent(request.request_id)}/confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          // The digest of the draft on screen. The controller refuses a
          // mismatch, so a draft revised in another tab cannot be confirmed
          // under the version this page is showing.
          body: JSON.stringify({ config_digest: request.config_digest }),
        },
      );
      const parsed = await response.json();
      if (!response.ok) {
        setError(typeof parsed.detail === "string" ? parsed.detail : "could not start the analysis");
        return;
      }
      startedAtRef.current = Date.now();
      setStatus(await refresh(request.request_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start the analysis");
    } finally {
      setConfirming(false);
    }
  }, [draft, confirming, refresh]);

  const request = draft?.request;
  const liveStatus = status?.status ?? request?.status ?? null;
  const runId = status?.scientific_run_id ?? request?.scientific_run_id ?? null;
  const started = Boolean(runId) || (liveStatus !== null && liveStatus !== "draft" &&
    liveStatus !== "awaiting_confirmation" && liveStatus !== "rejected");
  const blockingQuestions = (request?.missing_questions ?? []).filter((q) => q.required);
  const canConfirm = Boolean(draft?.can_confirm) && !started && !confirming;

  return (
    // Laid out inline rather than with a class in `globals.css`. Two columns
    // where there is room, stacked where there is not; the conversation and
    // the draft are meant to be read side by side, and the draft is the one
    // that must stay legible when there is not room for both.
    <div
      style={{
        display: "grid",
        gap: "1rem",
        gridTemplateColumns: "repeat(auto-fit, minmax(24rem, 1fr))",
        alignItems: "start",
      }}
    >
      <section className="panel" data-testid="intake-conversation">
        <h2 style={{ marginTop: 0 }}>Describe the analysis</h2>
        {modelConfigured ? (
          // No vendor badge here either — see the note in AssistantPanel.
          <CopilotKit runtimeUrl="/api/copilotkit?mode=intake" showDevConsole={false}>
            <div style={{ height: "32rem" }}>
              <CopilotChat
                instructions={`${instructions}\n\nThis conversation's id is ${conversationId}; pass it as conversation_id when you prepare a request.`}
                labels={{
                  title: "New analysis",
                  initial:
                    "Tell me what you want to analyse — which data, which species, and what the " +
                    "question is. I will prepare a request for you to review. I cannot start it: " +
                    "you press Confirm.",
                }}
              />
            </div>
          </CopilotKit>
        ) : (
          <div data-testid="assistant-unconfigured">
            <p>
              <strong>Assistant model is not configured.</strong>
            </p>
            <p className="subtle">{modelReason ?? "Set ASSISTANT_MODEL_BASE_URL and ASSISTANT_MODEL_NAME."}</p>
            <p className="subtle">
              The form below still works: it posts the same preview the assistant would have, so a
              request can be prepared and confirmed with no model at all.
            </p>
          </div>
        )}

        <details style={{ marginTop: "1rem" }}>
          <summary>Prepare a request without the assistant</summary>
          <ManualForm
            datasets={datasets}
            studyDesigns={studyDesigns}
            onSubmit={previewFromForm}
            requestId={request?.request_id ?? null}
          />
        </details>
      </section>

      <section className="panel" data-testid="draft-card">
        <h2 style={{ marginTop: 0 }}>Proposed analysis</h2>
        {!request && (
          <p className="subtle" data-testid="draft-empty">
            Nothing prepared yet. Describe what you want, or use the form, and the request will
            appear here for you to check before anything runs.
          </p>
        )}

        {request && (
          <>
            <Field label="Request" value={<code>{request.request_id}</code>} />
            <Field label="Status" value={<strong data-testid="request-status">{liveStatus}</strong>} />
            <Field label="Data" value={<code>{request.input_ref}</code>} missing={!request.input_ref} />
            <Field label="Project" value={request.project} missing={!request.project} />
            <Field label="Species" value={request.species} missing={!request.species} />
            <Field
              label="Research question"
              value={request.research_question}
              missing={!request.research_question}
            />
            <Field
              label="Study design"
              value={<code>{request.study_design_ref}</code>}
              missing={!request.study_design_ref}
            />
            <Field label="Settings" value={<AnalysisList analysis={request.analysis} />} />
            <Field
              label="Config digest"
              value={<code style={{ fontSize: "0.75rem" }}>{request.config_digest}</code>}
            />

            {draft?.execution_plan && (
              <div style={{ marginTop: "1rem" }}>
                <h3>What would run</h3>
                <p className="subtle" style={{ marginTop: 0 }}>
                  {draft.execution_plan.steps.length} steps, entering by the{" "}
                  <strong>{draft.execution_plan.route}</strong> route.{" "}
                  {draft.execution_plan.note}
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                  {draft.execution_plan.steps.map((step) => (
                    <code key={step} style={{ fontSize: "0.72rem" }}>
                      {step}
                    </code>
                  ))}
                </div>
                {draft.execution_plan.gates.length > 0 && (
                  <>
                    <h3>Where it will stop and ask you</h3>
                    <ul style={{ marginTop: 0 }}>
                      {draft.execution_plan.gates.map((gate) => (
                        <li key={gate.step}>
                          <code>{gate.step}</code> — {gate.why}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}

            {blockingQuestions.length > 0 && (
              <div className="panel" data-tone="warn" data-testid="missing-questions">
                <h3 style={{ marginTop: 0 }}>Still needed</h3>
                <ul style={{ marginBottom: 0 }}>
                  {blockingQuestions.map((q) => (
                    <li key={q.field}>
                      <strong>{q.field}</strong> — {q.question}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {request.validation_errors.length > 0 && (
              <div className="panel" data-tone="fail" data-testid="validation-errors">
                <h3 style={{ marginTop: 0 }}>Cannot run</h3>
                <ul style={{ marginBottom: 0 }}>
                  {request.validation_errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}

            {request.unsupported.length > 0 && (
              <div className="panel" data-tone="fail" data-testid="unsupported">
                <h3 style={{ marginTop: 0 }}>Not supported by this workflow</h3>
                <ul style={{ marginBottom: 0 }}>
                  {request.unsupported.map((u, i) => (
                    <li key={i}>{u}</li>
                  ))}
                </ul>
              </div>
            )}

            {request.warnings.length > 0 && (
              <div className="panel" data-tone="warn" data-testid="warnings">
                <h3 style={{ marginTop: 0 }}>Worth knowing</h3>
                <ul style={{ marginBottom: 0 }}>
                  {request.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <div style={{ marginTop: "1.2rem", display: "flex", gap: "0.8rem", alignItems: "center" }}>
              <button
                type="button"
                onClick={confirm}
                disabled={!canConfirm}
                data-testid="confirm-button"
                aria-disabled={!canConfirm}
              >
                {confirming ? "Starting…" : started ? "Started" : "Confirm and start analysis"}
              </button>
              {operatorMode === "local" && (
                <span className="subtle">
                  will be recorded as <code>local-operator</code> — local development only
                </span>
              )}
              {operatorMode === "unavailable" && (
                <span className="subtle">no operator identity is configured; confirmation is refused</span>
              )}
            </div>

            {!canConfirm && !started && (
              <p className="subtle" data-testid="confirm-blocked-reason">
                {request.validation_errors.length > 0
                  ? "The request has errors that must be fixed first."
                  : blockingQuestions.length > 0
                    ? "Answer the questions above first."
                    : "Not ready to start."}
              </p>
            )}

            {error && (
              <p className="subtle" data-tone="fail" data-testid="confirm-error">
                {error}
              </p>
            )}
            {pollError && (
              <p className="subtle" data-testid="poll-error">
                {pollError}
              </p>
            )}

            {runId && (
              <div className="panel" data-testid="started-panel">
                <p style={{ marginTop: 0 }}>
                  Scientific run <code>{runId}</code>
                  {status?.job ? ` · job ${status.job.status}` : ""}
                </p>
                {status?.run?.status === "needs_review" && (
                  <p>
                    <strong>Waiting for your decision.</strong>{" "}
                    <Link href={`/runs/${encodeURIComponent(runId)}`}>Open the run →</Link>
                  </p>
                )}
                {status?.run?.status !== "needs_review" && (
                  <p>
                    <Link href={`/runs/${encodeURIComponent(runId)}`}>Open the run →</Link>
                  </p>
                )}
                {status?.job?.error && <p className="subtle">{status.job.error}</p>}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/**
 * A plain form that posts the same preview the assistant does.
 *
 * Not a fallback for a broken assistant so much as a statement about where the
 * authority is: the request is a structured object validated by the
 * controller, and a conversation is one way of filling it in, not the only
 * way. It is also what makes the page testable and usable with no model
 * configured at all.
 */
function ManualForm({
  datasets,
  studyDesigns,
  onSubmit,
  requestId,
}: {
  datasets: DatasetOption[];
  studyDesigns: StudyDesignOption[];
  onSubmit: (body: Record<string, unknown>) => Promise<void>;
  requestId: string | null;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <form
      data-testid="manual-form"
      onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const analysis: Record<string, unknown> = {};
        const method = String(form.get("embedding_method") || "");
        if (method) analysis.embedding_method = method;
        const resolution = String(form.get("resolution") || "");
        if (resolution) analysis.resolution = Number(resolution);
        const integration = String(form.get("integration_mode") || "");
        if (integration) analysis.integration_mode = integration;

        setBusy(true);
        try {
          await onSubmit({
            request_id: requestId,
            input_ref: String(form.get("input_ref") || "") || null,
            species: String(form.get("species") || "") || null,
            project: String(form.get("project") || "") || null,
            research_question: String(form.get("research_question") || "") || null,
            study_design_ref: String(form.get("study_design_ref") || "") || null,
            analysis,
          });
        } finally {
          setBusy(false);
        }
      }}
      style={{ display: "grid", gap: "0.6rem", marginTop: "0.8rem" }}
    >
      <label>
        Data
        <select name="input_ref" defaultValue="">
          <option value="">choose a dataset…</option>
          {datasets.map((d) => (
            <option key={d.input_ref} value={d.input_ref}>
              {d.display_name} ({d.kind})
            </option>
          ))}
        </select>
      </label>
      <label>
        Species
        <input name="species" placeholder="human" />
      </label>
      <label>
        Project name
        <input name="project" placeholder="PBMC demonstration" />
      </label>
      <label>
        Research question
        <input name="research_question" placeholder="which cell types are present" />
      </label>
      <label>
        Study design
        <select name="study_design_ref" defaultValue="">
          <option value="">none</option>
          {studyDesigns.map((s) => (
            <option key={s.study_design_ref} value={s.study_design_ref}>
              {s.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Embedding
        <select name="embedding_method" defaultValue="">
          <option value="">default (umap)</option>
          <option value="umap">umap</option>
          <option value="tsne">tsne</option>
          <option value="both">both</option>
        </select>
      </label>
      <label>
        Integration
        <select name="integration_mode" defaultValue="">
          <option value="">unset — the run will say so at the gate</option>
          <option value="none">none</option>
          <option value="harmony">harmony (needs a study design)</option>
        </select>
      </label>
      <label>
        Clustering resolution
        <input name="resolution" type="number" step="0.1" placeholder="1.0" />
      </label>
      <button type="submit" disabled={busy}>
        {busy ? "Checking…" : "Prepare request"}
      </button>
    </form>
  );
}

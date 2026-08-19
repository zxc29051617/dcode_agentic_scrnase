"use client";

import { useState } from "react";
import { CopilotKit, useCopilotChat } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { Role, TextMessage } from "@copilotkit/runtime-client-gql";
import "@copilotkit/react-ui/styles.css";

/**
 * An advisor beside the decision, not in a panel somewhere else.
 *
 * The question a person has at this moment — "which of these sixty-one fits my
 * data?" — is about the list they are looking at. Putting the conversation in
 * the side panel would mean reading an argument in one column and finding the
 * option it names in another, and the whole difficulty here is holding the two
 * together.
 *
 * ## It advises. It does not answer.
 *
 * Mounted at `?mode=gate`, which serves three read-only actions and no others:
 * the briefing, the step record, the report. `accept` / `revise` / `stop` are a
 * POST from a Route Handler behind a button, and nothing this component mounts
 * can reach it. The person picks in the list above and presses Submit.
 *
 * Collapsed by default. It costs 755 kB of `@copilotkit/react-ui` to open, and
 * a gate answered without reading any advice is a perfectly good outcome — most
 * gates are `accept` on a warning somebody already understands.
 */

function Openers({ runId, step, parameter }: { runId: string; step: string; parameter: string | null }) {
  const { appendMessage, isLoading } = useCopilotChat();

  // Questions worth asking about *this* gate, phrased as a person would.
  // The run id rides in the text as well as in the system prompt, so a
  // suggestion still resolves after somebody has scrolled the conversation.
  const asks = [
    parameter
      ? `For run ${runId}, which ${parameter} should I choose and why?`
      : `What is run ${runId} waiting for me to decide?`,
    `What do the marker genes in run ${runId} say about what this tissue is?`,
    `What is the second-best option for run ${runId}, and when would it be better?`,
    `Is the recommended option for run ${runId} already downloaded?`,
  ];

  return (
    <div className="suggestions" style={{ marginBottom: "0.6rem" }}>
      {asks.map((text) => (
        <button
          key={text}
          type="button"
          disabled={isLoading}
          onClick={() => void appendMessage(new TextMessage({ role: Role.User, content: text }))}
        >
          {text.replace(` for run ${runId}`, "").replace(`For run ${runId}, `, "").replace(` in run ${runId}`, "")}
        </button>
      ))}
    </div>
  );
}

export default function GateAdvisor({
  runId,
  step,
  parameter,
  instructions,
  modelConfigured,
  modelReason,
}: {
  runId: string;
  step: string;
  /** The one thing this gate lets a person change, or null. */
  parameter: string | null;
  instructions: string;
  modelConfigured: boolean;
  modelReason: string | null;
}) {
  const [open, setOpen] = useState(false);

  if (!modelConfigured) {
    return (
      <p className="subtle" data-testid="advisor-unconfigured" style={{ marginTop: "0.8rem" }}>
        No assistant model is configured, so there is no advice available here —{" "}
        {modelReason ?? "set ASSISTANT_MODEL_BASE_URL and ASSISTANT_MODEL_NAME"}. The options above
        are the executor&apos;s own list and the decision is unaffected.
      </p>
    );
  }

  if (!open) {
    return (
      <div style={{ marginTop: "0.9rem" }}>
        <button type="button" onClick={() => setOpen(true)} data-testid="advisor-open">
          Ask the assistant which to choose
        </button>
        <span className="subtle" style={{ marginLeft: "0.6rem" }}>
          it can argue for an option — it cannot pick one
        </span>
      </div>
    );
  }

  return (
    <div data-testid="gate-advisor" style={{ marginTop: "0.9rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "0.6rem", marginBottom: "0.5rem" }}>
        <strong>Assistant</strong>
        <span className="subtle">advises only; you pick above and press Submit</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button type="button" onClick={() => setOpen(false)} aria-label="Close the assistant">
          ×
        </button>
      </div>
      <CopilotKit runtimeUrl="/api/copilotkit?mode=gate" showDevConsole={false}>
        <Openers runId={runId} step={step} parameter={parameter} />
        <div style={{ height: "24rem", border: "1px solid var(--line)", borderRadius: "6px" }}>
          <CopilotChat
            instructions={`${instructions}\n\nThe run in front of the user is ${runId}. It is paused at the ${step} gate${
              parameter ? `, which lets them change ${parameter}` : ""
            }. When they say "this run" or "this decision" they mean that one. Call get_gate_briefing with run_id "${runId}" before recommending anything.`}
            labels={{
              title: `${step} — which option?`,
              initial:
                "Ask me which option fits your data. I will read the recorded evidence — the " +
                "marker genes, the species, the cluster count — and argue for one. I cannot " +
                "choose for you: you pick it above and press Submit.",
            }}
          />
        </div>
      </CopilotKit>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
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

/**
 * The questions worth asking at *this* gate.
 *
 * They used to be one list for every gate, written when the only gate with a
 * picker was `annotate_cells` — so a person standing at the QC filter was
 * offered "what do the marker genes say about what this tissue is", which is
 * about a step that has not run yet. An irrelevant suggestion is worse than
 * none: it is the page telling somebody they have misunderstood where they
 * are.
 */
function asksFor(runId: string, step: string, parameters: string[]): string[] {
  const first = parameters[0];
  if (step === "apply_cell_qc_filter") {
    return [
      `For run ${runId}, read the threshold table and recommend values for ${parameters.join(", ")} — say what each one would cost and why you would draw the line there.`,
      `For run ${runId}, what would accepting with no filtering at all cost me later?`,
      `For run ${runId}, is a mitochondrial median of this size normal for the tissue?`,
      `For run ${runId}, which of these cells would a reviewer object to keeping?`,
    ];
  }
  if (step === "annotate_cells" || step === "cross_check_annotation") {
    return [
      first
        ? `For run ${runId}, which ${first} should I choose and why?`
        : `What is run ${runId} waiting for me to decide?`,
      `What do the marker genes in run ${runId} say about what this tissue is?`,
      `What is the second-best option for run ${runId}, and when would it be better?`,
      `Is the recommended option for run ${runId} already downloaded?`,
    ];
  }
  return [
    first
      ? `For run ${runId}, which ${first} should I choose and why?`
      : `What is run ${runId} waiting for me to decide, and what are my options?`,
    `For run ${runId}, what did the reviewer actually measure here?`,
    `For run ${runId}, what happens if I just accept this?`,
  ];
}

function Openers({
  runId,
  step,
  parameters,
  autoAsk,
}: {
  runId: string;
  step: string;
  parameters: string[];
  /** Send the first question as soon as the advisor opens. */
  autoAsk: boolean;
}) {
  const { appendMessage, isLoading } = useCopilotChat();
  const asks = asksFor(runId, step, parameters);
  const [asked, setAsked] = useState(false);

  // Opening the advisor is already the person asking for advice. Making them
  // then choose a question before anything happens meant the panel opened
  // empty, which reads as "the assistant has nothing to say" — the exact
  // complaint this fixes. It fires once, and only on an explicit open.
  useEffect(() => {
    if (!autoAsk || asked || isLoading) return;
    setAsked(true);
    void appendMessage(new TextMessage({ role: Role.User, content: asks[0] }));
  }, [autoAsk, asked, isLoading, appendMessage, asks]);

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
  parameters,
  instructions,
  modelConfigured,
  modelReason,
}: {
  runId: string;
  step: string;
  /** Everything this gate lets a person change. Empty is a real state — some
   *  gates offer only accept or stop. */
  parameters: string[];
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
          Ask the assistant to read this evidence
        </button>
        <span className="subtle" style={{ marginLeft: "0.6rem" }}>
          it answers straight away, argues for an option, and cannot pick one
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
        <Openers runId={runId} step={step} parameters={parameters} autoAsk />
        <div style={{ height: "24rem", border: "1px solid var(--line)", borderRadius: "6px" }}>
          <CopilotChat
            instructions={`${instructions}\n\nThe run in front of the user is ${runId}. It is paused at the ${step} gate${
              parameters.length ? `, which lets them change ${parameters.join(", ")}` : ""
            }. When they say "this run" or "this decision" they mean that one. Call get_gate_briefing with run_id "${runId}" before recommending anything.`}
            labels={{
              title: `${step} — which option?`,
              initial:
                "I am reading what this step recorded. I will argue for an option and say what " +
                "it would cost — I cannot choose for you: you pick it above and press Submit.",
            }}
          />
        </div>
      </CopilotKit>
    </div>
  );
}

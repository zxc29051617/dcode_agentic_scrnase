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
type Ask = {
  /** What the button says. Short, because it is a button. */
  label: string;
  /** What is actually sent. Carries the run id, which the label does not. */
  message: string;
};

function asksFor(runId: string, step: string, parameters: string[]): Ask[] {
  const first = parameters[0];
  // The run id belongs in the message and not on the button. It used to be in
  // both, and the button text was produced by deleting the id back out of the
  // sentence with three chained `.replace()` calls — a transform that breaks
  // the moment the wording changes, silently, by showing the raw sentence.
  const ask = (label: string, message: string): Ask => ({ label, message: `第 ${runId} 次執行：${message}` });
  if (step === "apply_cell_qc_filter") {
    return [
      ask(
        "讀閾值表，建議數值",
        `讀這一關的閾值表，替 ${parameters.join("、")} 各建議一個值 —— 說明每個值會付出什麼代價，以及你為什麼把線畫在那裡。`,
      ),
      ask("完全不過濾會怎樣？", "如果我什麼都不過濾就接受，後面會付出什麼代價？"),
      ask("這個粒線體中位數正常嗎？", "這個組織的粒線體中位數落在這個大小，正常嗎？"),
      ask("審稿人會反對留下哪些細胞？", "這些細胞裡，哪些是審稿人會反對保留的？"),
    ];
  }
  if (step === "annotate_cells" || step === "cross_check_annotation") {
    return [
      first
        ? ask(`該選哪一個 ${first}？`, `我該選哪一個 ${first}，為什麼？`)
        : ask("這一關在等我決定什麼？", "這次執行在等我決定什麼？"),
      ask("marker 基因說這是什麼組織？", "這次執行的 marker 基因，說明這是什麼組織？"),
      ask("第二好的選項是什麼？", "第二好的選項是什麼？什麼情況下它會比較好？"),
      ask("推薦的那個下載了嗎？", "推薦的那個選項，本機已經下載了嗎？"),
    ];
  }
  return [
    first
      ? ask(`該選哪一個 ${first}？`, `我該選哪一個 ${first}，為什麼？`)
      : ask("這一關在等我決定什麼？", "這次執行在等我決定什麼？我有哪些選項？"),
    ask("模型實際量到了什麼？", "模型在這一關實際量到了什麼？"),
    ask("直接接受會怎樣？", "如果我直接接受，會發生什麼事？"),
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
    void appendMessage(new TextMessage({ role: Role.User, content: asks[0].message }));
  }, [autoAsk, asked, isLoading, appendMessage, asks]);

  return (
    <div className="suggestions" style={{ marginBottom: "0.6rem" }}>
      {asks.map((item) => (
        <button
          key={item.label}
          type="button"
          disabled={isLoading}
          onClick={() => void appendMessage(new TextMessage({ role: Role.User, content: item.message }))}
        >
          {item.label}
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
        <strong>助理</strong>
        <span className="subtle">只提供建議；選項在上面，由你按下送出</span>
        <span className="spacer" style={{ flex: 1 }} />
        <button type="button" onClick={() => setOpen(false)} aria-label="關閉助理">
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

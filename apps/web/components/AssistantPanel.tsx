"use client";

import { CopilotKit, useCopilotChat } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { Role, TextMessage } from "@copilotkit/runtime-client-gql";
import "@copilotkit/react-ui/styles.css";

/**
 * The read-only assistant, scoped to whichever run the page is showing.
 *
 * `runtimeUrl` is a same-origin Next.js route. The browser never learns
 * `GATEWAY_URL`, `ASSISTANT_MODEL_BASE_URL` or the API key: each is read
 * inside a Route Handler or a Server Component, and this component receives
 * only a run id and the instructions text.
 *
 * Mounted lazily by `AppShell` — see the note there about the bundle.
 */

const SUGGESTIONS = [
  "Explain the current warnings",
  "Summarize the QC results",
  "Which steps were reused?",
  "Show the provenance recorded for this run",
];

function Suggestions({ runId }: { runId: string | null }) {
  // `useCopilotChat` exposes `appendMessage`, which takes a constructed
  // `TextMessage` — `sendMessage` lives on the headless hook and is not part
  // of this return type.
  const { appendMessage, isLoading } = useCopilotChat();
  return (
    <div className="suggestions">
      {SUGGESTIONS.map((text) => (
        <button
          key={text}
          disabled={isLoading}
          onClick={() =>
            void appendMessage(
              new TextMessage({
                role: Role.User,
                // The run is named in the sent text as well as in the system
                // instructions, so a suggestion still means the right thing
                // if the user has scrolled back through the conversation.
                content: runId ? `${text} for run ${runId}.` : text,
              }),
            )
          }
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export default function AssistantPanel({
  runId,
  instructions,
}: {
  runId: string | null;
  instructions: string;
}) {
  const scoped = runId
    ? `${instructions}\n\nThe user is currently looking at scientific run ${runId}. When they say "this run" they mean ${runId}.`
    : instructions;

  return (
    <CopilotKit runtimeUrl="/api/copilotkit">
      <Suggestions runId={runId} />
      <div style={{ flex: 1, minHeight: 0 }}>
        <CopilotChat
          instructions={scoped}
          labels={{
            title: runId ? `Run ${runId}` : "Scientific runs",
            initial: runId
              ? `Ask about run ${runId}. I can only read recorded results.`
              : "Ask about the recorded runs. I can only read recorded results.",
          }}
        />
      </div>
    </CopilotKit>
  );
}

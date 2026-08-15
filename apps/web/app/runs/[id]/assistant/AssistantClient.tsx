"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";

/**
 * `runtimeUrl` names a same-origin Next.js route (`/api/copilotkit`), never
 * the gateway and never the model endpoint. The browser learns neither
 * `GATEWAY_URL` nor `ASSISTANT_MODEL_BASE_URL` nor the API key: every one of
 * those is read inside a Route Handler or a Server Component, and this
 * component is only ever handed the run id and the instructions text.
 *
 * This component is rendered only when a model is configured. The
 * unconfigured state is a different branch in `page.tsx` that does not mount
 * a chat at all, so an empty adapter can never be mistaken for a working
 * assistant.
 */
export default function AssistantClient({
  runId,
  instructions,
}: {
  runId: string;
  instructions: string;
}) {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit">
      <p style={{ color: "#666", fontSize: "0.85rem" }}>
        This assistant can only call <code>list_runs</code>,{" "}
        <code>get_run_snapshot</code>, <code>get_step_record</code>,{" "}
        <code>get_report</code> and <code>get_provenance</code>. It cannot
        change a threshold, answer a gate, or start anything — see
        app/api/copilotkit/route.ts.
      </p>
      <div style={{ height: "60vh", border: "1px solid #ddd" }}>
        <CopilotChat
          instructions={`${instructions}\n\nThe user is currently looking at scientific run ${runId}. When they say "this run" they mean ${runId}.`}
          labels={{ title: `Run ${runId}`, initial: `Ask about run ${runId}.` }}
        />
      </div>
    </CopilotKit>
  );
}

import AppShell, { type ShellRun } from "@/components/AppShell";
import { describeAssistantModel } from "@/lib/assistantModel";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";

/**
 * Server-side wrapper that reads the assistant's configuration and hands the
 * shell only what a browser may see.
 *
 * `describeAssistantModel()` returns the model name and a credential-stripped
 * endpoint, never the API key, so nothing secret crosses into the client
 * component tree. Every page renders inside this.
 */
export default function RunShell({
  run,
  children,
}: {
  run: ShellRun;
  children: React.ReactNode;
}) {
  const model = describeAssistantModel();
  return (
    <AppShell
      run={run}
      assistantConfigured={model.configured}
      assistantReason={model.configured ? null : model.reason}
      assistantModel={model.configured ? model.model : null}
      assistantEndpoint={model.configured ? model.endpoint : null}
      instructions={READ_ONLY_INSTRUCTIONS}
    >
      {children}
    </AppShell>
  );
}

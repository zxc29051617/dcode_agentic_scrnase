import AppShell, { type ShellRun } from "@/components/AppShell";
import { describeAssistantModel } from "@/lib/assistantModel";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";
import { controllerConfigured } from "@/lib/controller";

/**
 * Server-side wrapper that reads the assistant's configuration and hands the
 * shell only what a browser may see.
 *
 * `describeAssistantModel()` returns the model name and a credential-stripped
 * endpoint, never the API key, so nothing secret crosses into the client
 * component tree. `controllerConfigured()` returns a boolean, never the
 * controller's address. Every page renders inside this.
 *
 * The assistant mounted in the shell is always the **read-only** one, on every
 * page including `/analysis/new`. The intake assistant lives inside the intake
 * page's own panel with its own action set — see `app/api/copilotkit/route.ts`
 * for why the two sets are never merged.
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
      canStartAnalyses={controllerConfigured()}
    >
      {children}
    </AppShell>
  );
}

import RunShell from "@/components/RunShell";
import RunsTable from "@/components/RunsTable";
import { listRuns } from "@/lib/gateway";
import type { RunSummary } from "@/lib/gatewayTypes";

export const dynamic = "force-dynamic";

export default async function RunsPage() {
  let runs: RunSummary[];
  let error: string | null = null;
  try {
    runs = await listRuns();
  } catch (e) {
    // The gateway being down is the single most likely reason this page is
    // empty, and a blank table would say nothing about it. `lib/gateway.ts`
    // puts the URL and a "is it running?" prompt in the message.
    runs = [];
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <RunShell run={null}>
      <h1>Scientific runs</h1>
      <p className="subtle">
        Everything on this site is read from recorded run directories. Nothing here starts a run,
        resumes one, or answers a human gate.
      </p>

      {error ? (
        <div className="panel" data-tone="warn">
          <h2 style={{ marginTop: 0 }}>Cannot reach the gateway</h2>
          <p style={{ margin: 0 }}>{error}</p>
        </div>
      ) : (
        <RunsTable runs={runs} />
      )}
    </RunShell>
  );
}

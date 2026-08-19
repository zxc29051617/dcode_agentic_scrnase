import RunShell from "@/components/RunShell";
import AnalysisIntake from "@/components/AnalysisIntake";
import { describeAssistantModel } from "@/lib/assistantModel";
import { INTAKE_INSTRUCTIONS } from "@/lib/intakeActions";
import { controllerConfigured, listCatalog } from "@/lib/controller";
import { resolveOperator } from "@/lib/operator";
import type { DatasetOption, StudyDesignOption } from "@/lib/controllerTypes";

export const dynamic = "force-dynamic";

/**
 * The one page in this app from which an analysis can be started.
 *
 * Everything it needs from the server is read here, in a Server Component, and
 * handed down as plain data: the dataset catalog (references and display names,
 * never paths), whether a model is configured (never the key), and which mode
 * the operator identity is in. `ANALYSIS_CONTROLLER_URL`, `GATEWAY_URL` and
 * `ASSISTANT_MODEL_API_KEY` are read inside `server-only` modules and none of
 * them crosses into the component tree.
 *
 * The page renders in three states, and the unconfigured ones say so plainly
 * rather than presenting a form that cannot work:
 *
 *   - no controller  — this deployment is read-only; the CLI is how to start a run
 *   - no model       — the conversation is unavailable, the form still works
 *   - configured     — both
 */
export default async function NewAnalysisPage() {
  const model = describeAssistantModel();
  const operator = resolveOperator();

  if (!controllerConfigured()) {
    return (
      <RunShell run={null}>
        <h1>New analysis</h1>
        <div className="panel" data-tone="warn" data-testid="controller-unconfigured">
          <p style={{ marginTop: 0 }}>
            <strong>The analysis controller is not configured.</strong>
          </p>
          <p>
            This deployment can read recorded runs but cannot start one.{" "}
            <code>ANALYSIS_CONTROLLER_URL</code> is unset — see{" "}
            <code>apps/web/.env.local.example</code> and{" "}
            <code>services/controller/README.md</code>.
          </p>
          <p className="subtle" style={{ marginBottom: 0 }}>
            Runs can still be started from the terminal:{" "}
            <code>python -m src.run --input &lt;path&gt; --species human --interactive</code>
          </p>
        </div>
      </RunShell>
    );
  }

  let datasets: DatasetOption[] = [];
  let studyDesigns: StudyDesignOption[] = [];
  let rejected: { name: string; reason: string }[] = [];
  let catalogError: string | null = null;
  try {
    const catalog = await listCatalog();
    datasets = catalog.datasets;
    studyDesigns = catalog.study_designs;
    rejected = catalog.rejected ?? [];
  } catch (error) {
    // The controller being down is the most likely reason this page is empty,
    // and an empty dataset list would say nothing about it.
    catalogError = error instanceof Error ? error.message : String(error);
  }

  return (
    <RunShell run={null}>
      <h1>New analysis</h1>
      <p className="subtle">
        Describe what you want analysed. The assistant prepares a request and this page shows
        exactly what would run. <strong>Nothing starts until you press Confirm</strong> — the
        assistant cannot press it, and it cannot answer the human gates the run will stop at.
      </p>

      {catalogError && (
        <div className="panel" data-tone="fail" data-testid="catalog-error">
          <p style={{ margin: 0 }}>{catalogError}</p>
        </div>
      )}

      {/* A dropped catalog entry names itself and its reason, never its path.
          Without this an empty list is indistinguishable from a mistyped one,
          which is the most likely thing to go wrong when setting this up. */}
      {rejected.length > 0 && (
        <div className="panel" data-tone="warn" data-testid="catalog-rejected">
          <h2 style={{ marginTop: 0 }}>Some catalog entries were not accepted</h2>
          <ul style={{ marginBottom: 0 }}>
            {rejected.map((entry) => (
              <li key={entry.name}>
                <code>{entry.name}</code> — {entry.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {datasets.length === 0 && !catalogError && (
        <div className="panel" data-tone="warn">
          <p style={{ margin: 0 }}>
            The controller offers no datasets. Set <code>CONTROLLER_CATALOG</code> and{" "}
            <code>CONTROLLER_DATA_ROOTS</code> — see <code>services/controller/README.md</code>.
            A relative path in the catalog is taken relative to the repository root.
          </p>
        </div>
      )}

      <AnalysisIntake
        datasets={datasets}
        studyDesigns={studyDesigns}
        instructions={INTAKE_INSTRUCTIONS}
        operatorMode={operator.ok ? operator.mode : "unavailable"}
        modelConfigured={model.configured}
        modelReason={model.configured ? null : model.reason}
      />
    </RunShell>
  );
}

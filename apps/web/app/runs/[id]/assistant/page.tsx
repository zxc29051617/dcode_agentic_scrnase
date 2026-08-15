import Link from "next/link";
import { notFound } from "next/navigation";
import { getRunSnapshot } from "@/lib/gateway";
import { describeAssistantModel } from "@/lib/assistantModel";
import { READ_ONLY_INSTRUCTIONS } from "@/lib/assistantActions";
import AssistantClient from "./AssistantClient";

export const dynamic = "force-dynamic";

export default async function AssistantPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const snapshot = await getRunSnapshot(id);
  if (!snapshot) notFound();

  // Read server-side and reduced to a shape with no credential in it before
  // it crosses into the React tree. `describeAssistantModel()` returns the
  // model name and a credential-stripped endpoint, never the API key.
  const model = describeAssistantModel();

  return (
    <main>
      <p>
        <Link href={`/runs/${encodeURIComponent(id)}`}>&larr; {id}</Link>
      </p>
      <h1>Assistant — {id}</h1>

      {model.configured ? (
        <>
          <p style={{ color: "#666", fontSize: "0.85rem" }}>
            model: <code>{model.model}</code> at <code>{model.endpoint}</code>
          </p>
          <AssistantClient runId={id} instructions={READ_ONLY_INSTRUCTIONS} />
        </>
      ) : (
        <section
          style={{ border: "1px solid #c0392b", padding: "1rem", margin: "1rem 0" }}
          role="status"
        >
          <h2 style={{ marginTop: 0 }}>Assistant model is not configured</h2>
          <p>
            Chat is unavailable: {model.reason}.
          </p>
          <p style={{ fontSize: "0.9rem" }}>
            Set <code>ASSISTANT_MODEL_BASE_URL</code>, <code>ASSISTANT_MODEL_NAME</code>{" "}
            and (if your endpoint requires one) <code>ASSISTANT_MODEL_API_KEY</code> in{" "}
            <code>apps/web/.env.local</code>, then restart the server. See{" "}
            <code>.env.local.example</code>.
          </p>
          <p style={{ fontSize: "0.9rem", color: "#666" }}>
            The rest of this run&apos;s pages — status, timeline, report and provenance
            — do not need a model and work regardless.
          </p>
        </section>
      )}
    </main>
  );
}

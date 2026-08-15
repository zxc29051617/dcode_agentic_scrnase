import Link from "next/link";
import { notFound } from "next/navigation";
import { getRunSnapshot, getStepRecords } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, steps] = await Promise.all([getRunSnapshot(id), getStepRecords(id)]);

  if (!snapshot) notFound();

  return (
    <main>
      <p>
        <Link href="/runs">&larr; all runs</Link>
      </p>
      <h1>{snapshot.scientific_run_id}</h1>
      <p>
        status: <strong>{snapshot.status}</strong>
        {snapshot.species ? ` · species: ${snapshot.species}` : ""}
        {snapshot.started_at ? ` · started: ${snapshot.started_at}` : ""}
      </p>

      {snapshot.pending_gate && (
        <section style={{ border: "1px solid #d9a441", padding: "1rem", margin: "1rem 0" }}>
          <h2>Pending human gate</h2>
          <p>
            waiting at <code>{snapshot.pending_gate.step}</code> ({snapshot.pending_gate.gate})
            — verdict <strong>{snapshot.pending_gate.verdict}</strong>
          </p>
          <ul>
            {snapshot.pending_gate.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
          <p style={{ color: "#666", fontSize: "0.85rem" }}>
            This page has no accept / revise / stop control. Answering this gate
            happens at the terminal, per{" "}
            <code>docs/copilotkit_product_architecture.md</code> §1.4.
          </p>
        </section>
      )}

      <h2>Workflow timeline</h2>
      <ol>
        {(steps ?? []).map((s) => (
          <li key={s.step}>
            <code>{s.step}</code> — {s.status}
            {s.verdict ? ` — judge: ${s.verdict.verdict} (${s.verdict.score})` : ""}
            {s.output_summary.warnings.length > 0 && (
              <span style={{ color: "#a66" }}> — {s.output_summary.warnings.length} warning(s)</span>
            )}
          </li>
        ))}
      </ol>

      <p>
        <Link href={`/runs/${encodeURIComponent(id)}/report`}>report</Link>
        {" · "}
        <Link href={`/runs/${encodeURIComponent(id)}/assistant`}>assistant</Link>
      </p>
    </main>
  );
}

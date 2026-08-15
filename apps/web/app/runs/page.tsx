import Link from "next/link";
import { listRuns } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export default async function RunsPage() {
  const runs = await listRuns();

  return (
    <main>
      <h1>Scientific runs</h1>
      {runs.length === 0 ? (
        <p>No runs found under the configured gateway runs root.</p>
      ) : (
        <table cellPadding={8} style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
              <th>Run</th>
              <th>Status</th>
              <th>Started</th>
              <th>Steps recorded</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.scientific_run_id} style={{ borderBottom: "1px solid #eee" }}>
                <td>
                  <Link href={`/runs/${encodeURIComponent(r.scientific_run_id)}`}>
                    {r.scientific_run_id}
                  </Link>
                </td>
                <td>{r.status}</td>
                <td>{r.started_at ?? "—"}</td>
                <td>{r.steps_recorded}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}

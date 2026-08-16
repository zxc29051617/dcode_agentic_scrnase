import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import { getProvenance, getRunSnapshot } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** A recorded value, or a stated absence — never a blank that reads as zero. */
function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="subtle">Not recorded</span>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="subtle">Not recorded</span>;
    return (
      <dl className="kv">
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd>
              <Value value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

export default async function ProvenancePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, provenance] = await Promise.all([getRunSnapshot(id), getProvenance(id)]);
  if (!snapshot || !provenance) notFound();

  const sections: { title: string; body: unknown; note?: string }[] = [
    { title: "Source", body: provenance.source, note: "commit, branch, dirty state, config and its digest" },
    { title: "Packages", body: provenance.packages },
    { title: "Seeds", body: provenance.seeds },
    { title: "Study design", body: provenance.study_design, note: "counts and digests only; never rows" },
    { title: "Judge sessions", body: provenance.judge_sessions },
    { title: "Revisions", body: provenance.revisions },
  ];

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Provenance</h1>
      <p className="subtle">
        What the run recorded about itself. The command line is deliberately not projected — on a
        real run it can carry a local absolute path.
      </p>

      {sections.map((section) => (
        <div className="panel" key={section.title}>
          <h2 style={{ marginTop: 0 }}>{section.title}</h2>
          {section.note && (
            <p className="subtle" style={{ marginTop: 0 }}>
              {section.note}
            </p>
          )}
          {Array.isArray(section.body) ? (
            section.body.length === 0 ? (
              <p className="subtle" style={{ margin: 0 }}>
                Not recorded
              </p>
            ) : (
              section.body.map((entry, i) => (
                <div key={i} style={{ marginBottom: "0.6rem" }}>
                  <Value value={entry} />
                </div>
              ))
            )
          ) : (
            <Value value={section.body} />
          )}
        </div>
      ))}
    </RunShell>
  );
}

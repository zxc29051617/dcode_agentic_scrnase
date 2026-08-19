import type { Provenance } from "@/lib/gatewayTypes";

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

/**
 * What the run recorded about itself — the tier `docs/report_contract.md`
 * calls "who decided what, and can it be rerun".
 *
 * Last in the document because it is the least often read and the most often
 * needed: nobody opens a run to read its package versions, and somebody
 * reproducing it needs nothing else. Collapsed by default for the same reason,
 * with the count in the summary so it is obvious there is something inside.
 */
export default function Provenance({ provenance }: { provenance: Provenance | null }) {
  if (!provenance) {
    return (
      <p className="subtle">
        This run recorded no provenance document. That is itself worth knowing — it means the run
        predates provenance recording or did not finish writing it.
      </p>
    );
  }

  const sections: { title: string; body: unknown; note?: string }[] = [
    {
      title: "Source",
      body: provenance.source,
      note: "commit, branch, dirty state, config and its digest",
    },
    { title: "Packages", body: provenance.packages },
    { title: "Seeds", body: provenance.seeds },
    { title: "Study design", body: provenance.study_design, note: "counts and digests only; never rows" },
    { title: "Judge sessions", body: provenance.judge_sessions },
    { title: "Revisions", body: provenance.revisions },
  ];

  return (
    <>
      <p className="subtle">
        Enough to run this again and get this. The command line is deliberately not projected — on
        a real run it can carry a local absolute path.
      </p>
      {sections.map((section) => (
        <details className="panel" key={section.title}>
          <summary>
            {section.title}
            {section.note && <span className="subtle"> — {section.note}</span>}
          </summary>
          <div style={{ marginTop: "0.7rem" }}>
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
        </details>
      ))}
    </>
  );
}

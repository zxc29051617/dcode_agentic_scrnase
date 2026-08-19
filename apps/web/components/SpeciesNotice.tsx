import type { SpeciesCatalogView } from "@/lib/controllerTypes";

/**
 * What choosing a species costs, said before anybody chooses one.
 *
 * This exists because the expensive part of a species is invisible at the
 * moment it is picked. Typing `rat` into a form is exactly as easy as typing
 * `human`, and the difference between them is a 30 GB reference somebody has
 * to build, a marker database that does not cover the organism, and a set of
 * QC constants this project refuses to guess. None of that surfaces until
 * `resolve_reference` — by which point a person has already committed.
 *
 * Two distinctions do the work here, and both are ones the pipeline itself
 * makes:
 *
 * **Profiled vs recognised.** `src/species.py` carries vetted gene lists for
 * human and mouse only, and says why: "A wrong symbol here is worse than a
 * missing one: a missing one stops the run, a wrong one filters the wrong
 * cells and says nothing." Rat, pig, macaque, zebrafish and Drosophila are
 * understood and deliberately left without a profile — they run, once told
 * their constants.
 *
 * **Supported vs installed.** A species can be fully profiled and still have
 * no reference on this machine. Those are different problems with different
 * fixes, so they are shown as different facts.
 *
 * Everything rendered here is projected from `src/species.py` through the
 * controller. Nothing in this file restates it — a second copy would be a
 * second answer, and the one a person reads would be the unenforced one.
 */
export default function SpeciesNotice({ catalog }: { catalog: SpeciesCatalogView | null }) {
  if (!catalog) return null;

  if (!catalog.available) {
    // "Unknown" and "nothing is supported" call for opposite actions, so this
    // never degrades into an empty list.
    return (
      <details className="panel" data-tone="warn" data-testid="species-notice">
        <summary>Which species can be analysed — unavailable</summary>
        <p style={{ marginBottom: 0 }}>
          The controller could not read the pipeline&rsquo;s species table, so this page cannot
          say which species are supported. It is not that none are. See{" "}
          <code>services/controller/README.md</code>.
        </p>
      </details>
    );
  }

  const missing = catalog.profiled.filter((s) => !s.reference_present);

  return (
    <details className="panel" data-testid="species-notice">
      <summary>
        Which species can be analysed, and what another one needs
        {missing.length > 0 && (
          <span className="subtle">
            {" "}
            — {missing.length} of {catalog.profiled.length} references not installed here
          </span>
        )}
      </summary>

      <p className="subtle" style={{ marginTop: "0.6rem" }}>
        Two of these are different questions. <strong>Profiled</strong> means this pipeline has
        vetted mitochondrial and haemoglobin gene lists for the organism, so a species name alone
        is enough. <strong>Installed</strong> means the Cell Ranger reference is on this machine.
        A run needs both.
      </p>

      <table className="kv-table" data-testid="species-table">
        <thead>
          <tr>
            <th>Species</th>
            <th>Reference</th>
            <th>On this machine</th>
            <th>Marker cross-check</th>
          </tr>
        </thead>
        <tbody>
          {catalog.profiled.map((s) => (
            <tr key={s.species}>
              <td>
                <strong>{s.species}</strong>
              </td>
              <td>
                <code>{s.reference_dirname}</code>
                <br />
                <span className="subtle">
                  {s.how === "prebuilt" ? "10x ships it" : "built from FASTA + GTF"}
                  {s.disk_gb ? ` · ${s.disk_gb} GB unpacked` : ""}
                  {s.download_gb ? ` · ${s.download_gb} GB download` : ""}
                </span>
              </td>
              <td>
                {s.reference_present ? (
                  <span data-testid={`ref-present-${s.species}`}>present</span>
                ) : (
                  <span className="subtle" data-testid={`ref-absent-${s.species}`}>
                    not installed — see <code>reference/README.md</code>
                  </span>
                )}
              </td>
              <td>
                {s.marker_db ? (
                  <span className="subtle">PanglaoDB &ldquo;{s.marker_db}&rdquo;</span>
                ) : (
                  <span className="subtle">none — the cross-check degrades</span>
                )}
                {!s.qc_defaults_native && (
                  <>
                    <br />
                    <span className="subtle">
                      QC starting points were read off another species&rsquo; data; the run says so
                    </span>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {catalog.recognised.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.95rem", marginBottom: "0.3rem" }}>
            Recognised, but with no vetted gene lists
          </h3>
          <p className="subtle" style={{ marginTop: 0 }}>
            {catalog.recognised.join(", ")}. These run — the pipeline will ask for the constants in
            config rather than inventing them. A wrong mitochondrial gene symbol filters the wrong
            cells and reports nothing wrong, which is why they are not guessed.
          </p>
        </>
      )}

      <h3 style={{ fontSize: "0.95rem", marginBottom: "0.3rem" }}>To add another species</h3>
      <p className="subtle" style={{ marginTop: 0 }}>
        Two files from the <em>same source and the same assembly version</em>: a genome FASTA and a
        gene annotation GTF. Then <code>cellranger mkgtf</code> and <code>cellranger mkref</code>.
        No Python changes — the reference path is a config value. Budget roughly 30 GB of disk and
        a large-memory machine.
      </p>
      <p className="subtle" style={{ marginTop: 0 }}>
        <strong>The GTF has to satisfy all of these.</strong> Each one below fails silently: the
        run finishes and the number it reports is wrong.
      </p>
      <ol className="subtle" style={{ marginTop: 0, marginBottom: 0 }}>
        {catalog.gtf_requirements.map((item) => (
          <li key={item.requirement} style={{ marginBottom: "0.35rem" }}>
            {item.requirement} — {item.why}
          </li>
        ))}
      </ol>
    </details>
  );
}

import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import QCTabs from "@/components/QCTabs";
import FigureGallery from "@/components/FigureGallery";
import { getRunSnapshot, getStepRecords, listArtifacts } from "@/lib/gateway";

/**
 * Report figures that are about quality rather than about results.
 *
 * `build_report` names its figures by the section they belong to — `m2_qc`,
 * `a2_qc_per_sample`, `a3_filter_reasons`, `a4_doublets`, `a5_pca_hvg` — and
 * the QC ones are the appendix tier plus M2. Matched by prefix rather than by
 * a hardcoded list of filenames, so a run that produces a figure this code
 * has never heard of still lands it in the right place.
 */
const QC_FIGURE_PREFIXES = ["m1_", "m2_", "a1_", "a2_", "a3_", "a4_", "a5_"];

export const dynamic = "force-dynamic";

export default async function QCPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [snapshot, steps, artifacts] = await Promise.all([
    getRunSnapshot(id),
    getStepRecords(id),
    listArtifacts(id),
  ]);
  if (!snapshot) notFound();

  const entries = artifacts ?? [];
  const detailFor = (step: string) => steps?.find((s) => s.step === step)?.upstream_detail;

  const fastqDetail = detailFor("fastq_qc");
  const cellrangerDetail = detailFor("cellranger_count");

  // The headline QC numbers, taken from what each step recorded. Absent
  // rather than zero when a step never ran — see SummaryCards.
  const allFigures = entries.filter((e) => e.kind === "figure");
  const qcFigures = allFigures.filter((e) =>
    QC_FIGURE_PREFIXES.some((prefix) => e.name.startsWith(prefix)),
  );

  const perRole = fastqDetail?.per_read_role ?? {};
  const r2 = perRole["R2"];
  const cellranger = cellrangerDetail?.libraries?.[0]?.metrics_summary ?? {};

  return (
    <RunShell run={{ id, status: snapshot.status, hasReport: snapshot.has_report }}>
      <h1>Quality control</h1>
      <p className="subtle">
        What FastQC, MultiQC and Cell Ranger recorded for this run. Reports produced by those tools
        are shown in an isolated frame; nothing here re-runs or recomputes anything.
      </p>

      <SummaryCards
        items={[
          {
            label: "cDNA (R2) Q30",
            value: r2?.q30_fraction != null ? `${(r2.q30_fraction * 100).toFixed(1)}%` : null,
            title: "mean across R2 files, from FastQC",
          },
          {
            label: "R2 duplication",
            value:
              r2?.duplicate_fraction != null
                ? `${(r2.duplicate_fraction * 100).toFixed(1)}%`
                : null,
            title: "expected to be high for scRNA-seq; UMIs collapse the copies",
          },
          {
            label: "Estimated cells",
            value: cellranger["Estimated Number of Cells"] ?? null,
            title: "Cell Ranger metrics_summary.csv",
          },
          {
            label: "Mapped to transcriptome",
            value: cellranger["Reads Mapped Confidently to Transcriptome"] ?? null,
            title: "Cell Ranger metrics_summary.csv",
          },
          {
            label: "Sequencing saturation",
            value: cellranger["Sequencing Saturation"] ?? null,
            title: "Cell Ranger metrics_summary.csv",
          },
          {
            label: "QC reports available",
            value: entries.filter(
              (e) => e.is_html && e.kind !== "report_html" && e.kind !== "embedding_html",
            ).length,
          },
        ]}
      />

      <QCTabs
        runId={id}
        fastqc={entries.filter((e) => e.kind === "fastqc_html")}
        multiqc={entries.filter((e) => e.kind === "multiqc_html")}
        cellranger={entries.filter((e) => e.kind === "cellranger_web_summary")}
        fastqDetail={fastqDetail}
        cellrangerDetail={cellrangerDetail}
        otherArtifactCount={entries.filter((e) => e.kind === "figure" || e.kind === "report_html").length}
      />

      <h2>QC figures from the report</h2>
      <p className="subtle">
        These come from <code>build_report</code>, not from FastQC — so a run started from a count
        matrix has them even though it has no upstream sequencing QC.
      </p>
      <div className="panel">
        <FigureGallery
          runId={id}
          figures={qcFigures}
          emptyReason={
            allFigures.length === 0
              ? "This run published no figures. build_report writes them, and a run that halted before it has none."
              : "None of this run's figures are QC figures. See the report for the rest."
          }
        />
      </div>
    </RunShell>
  );
}

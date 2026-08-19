import SummaryCards from "@/components/SummaryCards";
import QCTabs from "@/components/QCTabs";
import FigureGallery from "@/components/FigureGallery";
import type { ArtifactEntry, StepRecord } from "@/lib/gatewayTypes";

/**
 * Report figures that are about quality rather than about results.
 *
 * `build_report` names its figures by the section they belong to — `m2_qc`,
 * `a2_qc_per_sample`, `a3_filter_reasons`, `a4_doublets`, `a5_pca_hvg` — and
 * the QC ones are the appendix tier plus M1/M2. Matched by prefix rather than
 * by a hardcoded list of filenames, so a run that produces a figure this code
 * has never heard of still lands it in the right place.
 */
const QC_FIGURE_PREFIXES = ["m1_", "m2_", "a1_", "a2_", "a3_", "a4_", "a5_"];

/**
 * What the sequencing and counting tools recorded, as a section of the run
 * document rather than a page of its own.
 *
 * It sits after the findings and before the step-by-step, which is the order a
 * methods section uses: here is the result, here is why you can believe the
 * input to it, here is exactly what was done.
 */
export default function Quality({
  runId,
  steps,
  artifacts,
}: {
  runId: string;
  steps: StepRecord[] | null;
  artifacts: ArtifactEntry[];
}) {
  const detailFor = (step: string) => steps?.find((s) => s.step === step)?.upstream_detail;
  const fastqDetail = detailFor("fastq_qc");
  const cellrangerDetail = detailFor("cellranger_count");

  const allFigures = artifacts.filter((e) => e.kind === "figure");
  const qcFigures = allFigures.filter((e) =>
    QC_FIGURE_PREFIXES.some((prefix) => e.name.startsWith(prefix)),
  );

  const perRole = fastqDetail?.per_read_role ?? {};
  const r2 = perRole["R2"];
  const cellranger = cellrangerDetail?.libraries?.[0]?.metrics_summary ?? {};

  const hasUpstream = Boolean(fastqDetail || cellrangerDetail);

  return (
    <>
      <p className="subtle">
        {hasUpstream
          ? "What FastQC, MultiQC and Cell Ranger recorded. Their own reports are shown in an isolated frame; nothing here re-runs or recomputes anything."
          : "This run started from a count matrix, so there is no sequencing QC to show — that happens before a matrix exists. The figures below come from the pipeline's own QC steps."}
      </p>

      {hasUpstream && (
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
              value: artifacts.filter(
                (e) => e.is_html && e.kind !== "report_html" && e.kind !== "embedding_html",
              ).length,
            },
          ]}
        />
      )}

      {hasUpstream && (
        <QCTabs
          runId={runId}
          fastqc={artifacts.filter((e) => e.kind === "fastqc_html")}
          multiqc={artifacts.filter((e) => e.kind === "multiqc_html")}
          cellranger={artifacts.filter((e) => e.kind === "cellranger_web_summary")}
          fastqDetail={fastqDetail}
          cellrangerDetail={cellrangerDetail}
          otherArtifactCount={
            artifacts.filter((e) => e.kind === "figure" || e.kind === "report_html").length
          }
        />
      )}

      <div className="panel">
        <FigureGallery
          runId={runId}
          figures={qcFigures}
          emptyReason={
            allFigures.length === 0
              ? "This run published no figures. build_report writes them, and a run that stopped before it has none."
              : "None of this run's figures are QC figures. The rest are in the report above."
          }
        />
      </div>
    </>
  );
}

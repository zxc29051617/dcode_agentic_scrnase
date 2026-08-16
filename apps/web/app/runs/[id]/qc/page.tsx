import { notFound } from "next/navigation";
import RunShell from "@/components/RunShell";
import SummaryCards from "@/components/SummaryCards";
import QCTabs from "@/components/QCTabs";
import { getRunSnapshot, getStepRecords, listArtifacts } from "@/lib/gateway";

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
            value: entries.filter((e) => e.is_html && e.kind !== "report_html").length,
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
      />
    </RunShell>
  );
}

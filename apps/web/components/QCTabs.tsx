"use client";

import { useState } from "react";
import ArtifactFrame from "@/components/ArtifactFrame";
import UpstreamQC from "@/components/UpstreamQC";
import type { ArtifactEntry, UpstreamDetail } from "@/lib/gatewayTypes";

/**
 * FastQC, MultiQC and Cell Ranger, each with the numbers the pipeline already
 * parsed and — when one exists — the tool's own report in a sandbox.
 *
 * A tab whose artifact was never produced says so. It does not render an
 * empty frame or a broken image: an absence with a stated reason is evidence,
 * an absence without one is indistinguishable from a bug.
 */

type Tab = {
  id: string;
  label: string;
  artifacts: ArtifactEntry[];
  detail?: UpstreamDetail;
  /** Why this tab could be empty, in this run's own terms. */
  absent: string;
};

export default function QCTabs({
  runId,
  fastqc,
  multiqc,
  cellranger,
  fastqDetail,
  cellrangerDetail,
  otherArtifactCount,
}: {
  runId: string;
  fastqc: ArtifactEntry[];
  multiqc: ArtifactEntry[];
  cellranger: ArtifactEntry[];
  fastqDetail?: UpstreamDetail;
  cellrangerDetail?: UpstreamDetail;
  /** Artifacts this run does have, so an empty tab can say where to look. */
  otherArtifactCount: number;
}) {
  const tabs: Tab[] = [
    {
      id: "fastqc",
      label: "FastQC",
      artifacts: fastqc,
      detail: fastqDetail,
      absent:
        "This run recorded no FastQC results. FastQC runs on the FASTQ entry route only — a run started from a count matrix never reaches it.",
    },
    {
      id: "multiqc",
      label: "MultiQC",
      artifacts: multiqc,
      absent:
        "No MultiQC report was produced. It is written by fastq_qc after FastQC, and is skipped when multiqc is not installed or the run took the matrix route.",
    },
    {
      id: "cellranger",
      label: "Cell Ranger",
      artifacts: cellranger,
      detail: cellrangerDetail,
      absent:
        "This run recorded no Cell Ranger output. cellranger_count runs on the FASTQ entry route only.",
    },
  ];

  const [active, setActive] = useState(tabs[0].id);
  const current = tabs.find((t) => t.id === active)!;
  const empty = current.artifacts.length === 0 && !current.detail;

  return (
    <>
      <div className="controls" role="tablist" aria-label="QC tools">
        {tabs.map((tab) => {
          const has = tab.artifacts.length > 0 || Boolean(tab.detail);
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={active === tab.id}
              onClick={() => setActive(tab.id)}
              data-variant={active === tab.id ? "primary" : undefined}
            >
              {tab.label}
              {!has && <span style={{ opacity: 0.7 }}> · none</span>}
            </button>
          );
        })}
      </div>

      <div role="tabpanel">
        {empty ? (
          <div className="panel">
            <h2 style={{ marginTop: 0 }}>Not recorded</h2>
            <p>{current.absent}</p>
            {/* Saying only "nothing here" leaves a reader unsure whether the
                run has no QC or the page is broken. Name what this run does
                have, and where. */}
            {otherArtifactCount > 0 ? (
              <p className="subtle" style={{ margin: 0 }}>
                This run did publish {otherArtifactCount} other artifact
                {otherArtifactCount === 1 ? "" : "s"} — its report figures and rendered report.
                See the QC figures below, the{" "}
                <a href={`/runs/${encodeURIComponent(runId)}/report`}>report</a>, or the full{" "}
                <a href={`/runs/${encodeURIComponent(runId)}/artifacts`}>artifact list</a>.
              </p>
            ) : (
              <p className="subtle" style={{ margin: 0 }}>
                This run published no artifact of any kind.
              </p>
            )}
          </div>
        ) : (
          <>
            {current.detail && (
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>Recorded numbers</h2>
                <UpstreamQC detail={current.detail} />
              </div>
            )}

            {current.artifacts.length > 0 ? (
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>
                  {current.label} report{current.artifacts.length > 1 ? "s" : ""}
                </h2>
                {current.artifacts.map((artifact) => (
                  <ArtifactFrame key={artifact.artifact_id} runId={runId} artifact={artifact} />
                ))}
              </div>
            ) : (
              <div className="panel">
                <p style={{ margin: 0 }} className="subtle">
                  The numbers above were recorded, but {current.label}&apos;s own HTML report was
                  not kept for this run.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

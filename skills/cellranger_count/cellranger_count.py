from __future__ import annotations

from typing import Any

TOOL_NAME = "cellranger_count"
INPUT_FIELDS = (
    "fastq_bundle",
    "samplesheet",
    "reference",
    "cellranger_config",
)
OUTPUT_FIELDS = (
    "run_dir",
    "bam",
    "raw_feature_bc_matrix",
    "filtered_feature_bc_matrix",
    "web_summary",
    "metrics_summary",
    "run_manifest",
    "preferred_h5ad",
    "warnings",
    "errors",
)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

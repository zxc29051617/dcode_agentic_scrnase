from __future__ import annotations

from typing import Any

TOOL_NAME = "sample_qc_triage"
INPUT_FIELDS = (
    "QC metrics CSV",
    "optional identity checks",
    "triage policy"
    )
OUTPUT_FIELDS = (
    "sample_flags",
    "summary",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

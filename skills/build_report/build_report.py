from __future__ import annotations

from typing import Any

TOOL_NAME = "build_report"
INPUT_FIELDS = (
    "final state",
    "artifacts",
    "report config"
    )
OUTPUT_FIELDS = (
    "html_report",
    "pdf_snapshot",
    "json_summary",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

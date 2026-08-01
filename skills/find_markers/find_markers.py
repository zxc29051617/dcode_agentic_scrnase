from __future__ import annotations

from typing import Any

TOOL_NAME = "find_markers"
INPUT_FIELDS = (
    "AnnData",
    "cluster labels",
    "marker config"
    )
OUTPUT_FIELDS = (
    "marker_table",
    "marker_summary",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

from __future__ import annotations

from typing import Any

TOOL_NAME = "annotate_cells"
INPUT_FIELDS = (
    "marker table",
    "reference evidence",
    "annotation policy"
    )
OUTPUT_FIELDS = (
    "labels",
    "confidence",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

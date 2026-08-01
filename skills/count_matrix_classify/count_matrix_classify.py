from __future__ import annotations

from typing import Any

TOOL_NAME = "count_matrix_classify"
INPUT_FIELDS = (
    "matrix_bundle",
    "source_hint",
    "config",
)
OUTPUT_FIELDS = (
    "matrix_class",
    "evidence",
    "needs_cell_calling",
    "recommended_next_tool",
    "warnings",
    "errors",
)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

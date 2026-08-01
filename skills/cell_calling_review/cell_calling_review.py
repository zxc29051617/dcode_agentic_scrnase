from __future__ import annotations

from typing import Any

TOOL_NAME = "cell_calling_review"
INPUT_FIELDS = (
    "raw matrix summary",
    "source state",
    "review policy"
    )
OUTPUT_FIELDS = (
    "cell_calling_state",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

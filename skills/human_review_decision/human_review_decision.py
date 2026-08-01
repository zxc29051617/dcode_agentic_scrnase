from __future__ import annotations

from typing import Any

TOOL_NAME = "human_review_decision"
INPUT_FIELDS = (
    "judge payload",
    "candidate labels",
    "decision context"
    )
OUTPUT_FIELDS = (
    "decision",
    "rationale",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

from __future__ import annotations

from typing import Any

TOOL_NAME = "judge_filtered_counts"
INPUT_FIELDS = (
    "step",
    "analysis_result",
    "artifacts",
    "policy"
    )
OUTPUT_FIELDS = (
    "step",
    "verdict",
    "score",
    "reasons",
    "evidence",
    "suggested_action",
    "needs_human_review"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

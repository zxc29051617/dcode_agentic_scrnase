from __future__ import annotations

from typing import Any

TOOL_NAME = "load_raw_counts"
INPUT_FIELDS = (
    "raw matrix bundle",
    "optional source hint",
    "load config"
    )
OUTPUT_FIELDS = (
    "adata",
    "source_state",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

from __future__ import annotations

from typing import Any

TOOL_NAME = "run_integration"
INPUT_FIELDS = (
    "AnnData",
    "integration config"
    )
OUTPUT_FIELDS = (
    "integrated_embedding",
    "integration_summary",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

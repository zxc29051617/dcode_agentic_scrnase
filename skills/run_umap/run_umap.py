from __future__ import annotations

from typing import Any

TOOL_NAME = "run_umap"
INPUT_FIELDS = (
    "AnnData",
    "UMAP config"
    )
OUTPUT_FIELDS = (
    "umap_coordinates",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

from __future__ import annotations

from typing import Any

TOOL_NAME = "normalize_hvg_prepare"
INPUT_FIELDS = (
    "AnnData",
    "normalization config"
    )
OUTPUT_FIELDS = (
    "normalized_adata",
    "hvgs",
    "prep_summary",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

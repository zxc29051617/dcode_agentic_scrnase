from __future__ import annotations

from typing import Any

TOOL_NAME = "run_pca"
INPUT_FIELDS = (
    "AnnData",
    "PCA config"
    )
OUTPUT_FIELDS = (
    "pca_embedding",
    "loadings",
    "variance_explained",
    "warnings",
    "errors",
    "recommended_next_tool"
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(f"{TOOL_NAME} is a scaffold")


def main() -> int:
    raise NotImplementedError(f"{TOOL_NAME} CLI is a scaffold")

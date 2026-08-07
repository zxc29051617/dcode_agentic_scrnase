"""Assemble what a person needs in order to sign off on the whole run.

This is the mainline gate (H2), and it asks a different question from the
escalation gate (H1). H1 asks "this one step warned — continue?". H2 asks
"here is the analysis; do we publish it?". Until now both were built the same
way, from the last judge verdict, so the final gate presented `annotate_cells`'
verdict and its evidence — on a real run, a warning about model choice next to
a catalogue of 61 CellTypist models — and asked a person to accept the entire
analysis on that basis. It said nothing about how many cells survived, what
cell types were found, which thresholds had been chosen, or what accepting
would produce.

This step builds that picture. It is deterministic and reads only what earlier
steps recorded, the same discipline `build_report` follows: no analysis, no
recomputation, nothing that could make the review disagree with the report it
precedes.

## It does not decide
The name is the decision it *supports*, not one it makes. `run()` returns the
question; the gate node puts it to a person and records the answer. A skill
that chose on their behalf would be the one thing this pipeline exists to
prevent.

## It does not block either
`apply_cell_qc_filter` and `cell_calling_review` refuse to pass an unresolved
choice downstream, because without it they have no output at all. This gate is
the opposite: a run that stopped short still produced real QC, clustering and
markers, and a person may quite reasonably want the report anyway. So an
unfinished analysis is stated as plainly as possible — `accepting_would` says
what the report will and will not contain — and the choice stays theirs.

Run standalone:
    python skills/human_review_decision/human_review_decision.py --run-dir runs/<run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import persistence  # noqa: E402

TOOL_NAME = "human_review_decision"
INPUT_FIELDS = (
    "artifacts",
    "run_dir",
)
OUTPUT_FIELDS = (
    "findings",
    "decisions_made",
    "open_concerns",
    "accepting_would",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: A cluster labelled this confidently or less is worth mentioning by name at
#: the final gate. Matches `annotate_cells`' own threshold, so the two cannot
#: disagree about which clusters are shaky.
LOW_CONFIDENCE_MEDIAN = 0.5

#: Steps that report a `*_state` of `needs_review` left a choice unmade. Naming
#: the field per step beats guessing at a convention.
UNRESOLVED_STATE_KEYS = {
    "cell_calling_review": "cell_calling_state",
    "apply_cell_qc_filter": "filter_state",
    "annotate_cells": "annotation_state",
}


def _findings(art: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers, taken from whichever step owns each of them."""
    clustering = (art.get("run_clustering") or {}).get("clustering_summary") or {}
    annotation = (art.get("annotate_cells") or {}).get("annotation_summary") or {}
    doublets = (art.get("detect_doublets") or {}).get("doublet_summary") or {}
    qc_filter = (art.get("apply_cell_qc_filter") or {}).get("filter_summary") or {}
    integration = (art.get("run_integration") or {}).get("integration_summary") or {}
    markers = (art.get("find_markers") or {}).get("marker_summary") or {}

    return {
        "cells_analysed": annotation.get("n_cells") or qc_filter.get("n_after"),
        "cells_removed_by_qc": qc_filter.get("n_removed"),
        "doublets_called": doublets.get("n_doublets"),
        "doublets_removed": doublets.get("removed"),
        "samples": integration.get("n_batches"),
        "integrated": integration.get("integrated"),
        "clusters": clustering.get("n_clusters"),
        "cell_types": annotation.get("n_cell_types"),
        "cell_type_counts": annotation.get("cell_type_counts") or {},
        "clusters_with_markers": markers.get("n_clusters_tested"),
    }


def _decisions_made(art: dict[str, Any]) -> list[dict[str, Any]]:
    """Values that could have been different, and where each came from.

    The same question `build_report`'s audit tier answers, asked before the
    report exists rather than after — a person signing off should see what they
    are signing off on, not read it afterwards.
    """
    made: list[dict[str, Any]] = []

    def add(parameter: str, value: Any, source: str) -> None:
        if value is not None:
            made.append({"parameter": parameter, "value": value, "source": source})

    review = art.get("cell_calling_review") or {}
    add("cells kept", review.get("n_cells"),
        (review.get("selection") or {}).get("chosen_by", "operator"))

    thresholds = (art.get("apply_cell_qc_filter") or {}).get("thresholds") or {}
    chosen_by = thresholds.get("chosen_by", "operator")
    for name, value in thresholds.items():
        if name != "chosen_by":
            add(name, value, chosen_by)

    for sample, entry in ((art.get("detect_doublets") or {}).get("per_sample") or {}).items():
        if entry.get("assessed"):
            add(f"expected doublet rate ({sample})", entry.get("expected_rate"),
                entry.get("expected_rate_source", "derived"))

    clustering = (art.get("run_clustering") or {}).get("clustering_summary") or {}
    add("Leiden resolution", clustering.get("resolution"), "config or default")

    annotation = (art.get("annotate_cells") or {}).get("annotation_summary") or {}
    add("CellTypist model", annotation.get("model"), "operator")
    return made


def _open_concerns(art: dict[str, Any]) -> list[str]:
    """Everything a person should weigh before saying yes.

    Warnings are collected from every step rather than only the last, because
    the reason to stop at a final gate is usually something that went by
    several steps ago.
    """
    concerns: list[str] = []

    for step, key in sorted(UNRESOLVED_STATE_KEYS.items()):
        state = (art.get(step) or {}).get(key)
        if state and state != "applied" and state.endswith("review"):
            concerns.append(f"{step}: {key} is {state!r} — a choice there was never made")

    for step, output in sorted(art.items()):
        if not isinstance(output, dict):
            continue
        for message in output.get("warnings") or []:
            concerns.append(f"{step}: {message}")

    per_cluster = (art.get("annotate_cells") or {}).get("per_cluster") or {}
    shaky = [
        name for name, entry in per_cluster.items()
        if (entry.get("median_conf_score") or 1.0) < LOW_CONFIDENCE_MEDIAN
    ]
    if shaky:
        concerns.append(
            f"{len(shaky)} cluster(s) carry a median annotation confidence below "
            f"{LOW_CONFIDENCE_MEDIAN}: {', '.join(sorted(shaky))}"
        )
    return concerns


def _accepting_would(art: dict[str, Any], findings: dict[str, Any]) -> dict[str, Any]:
    """What each answer actually does, spelled out rather than assumed."""
    annotated = (art.get("annotate_cells") or {}).get("annotation_state") == "annotated"
    filtered = (art.get("apply_cell_qc_filter") or {}).get("filter_state") == "applied"

    missing: list[str] = []
    if not annotated:
        missing.append("cell type annotation — the report will have clusters but no cell types")
    if not filtered:
        missing.append("QC filtering — every cell that was loaded is still in the analysis")

    return {
        "accept": "build the report from the analysis exactly as it stands",
        "revise": "go back and re-run the step this gate was reached from",
        "stop": "end the run without a report; the artifacts on disk are kept",
        "report_would_be_missing": missing,
        "report_would_contain": {
            "cells": findings.get("cells_analysed"),
            "clusters": findings.get("clusters"),
            "cell_types": findings.get("cell_types"),
        },
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    art = payload.get("artifacts") or {}
    if not art:
        return _result(errors=["no artifacts to review; nothing has run yet"])

    findings = _findings(art)
    accepting = _accepting_would(art, findings)
    warnings: list[str] = []
    if accepting["report_would_be_missing"]:
        warnings.append(
            "the analysis is incomplete: "
            + "; ".join(accepting["report_would_be_missing"])
        )

    return _result(
        findings=findings,
        decisions_made=_decisions_made(art),
        open_concerns=_open_concerns(art),
        accepting_would=accepting,
        warnings=warnings,
        next_tool="build_report",
        metrics={
            "n_decisions_made": len(_decisions_made(art)),
            "n_open_concerns": len(_open_concerns(art)),
            "analysis_complete": not accepting["report_would_be_missing"],
        },
    )


def _result(
    *,
    findings: dict[str, Any] | None = None,
    decisions_made: list[dict[str, Any]] | None = None,
    open_concerns: list[str] | None = None,
    accepting_would: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "findings": findings or {},
        "decisions_made": decisions_made or [],
        "open_concerns": open_concerns or [],
        "accepting_would": accepting_would or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "notes": [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("--run-dir", required=True,
                        help="a runs/<run_id> directory holding the completed steps")
    args = parser.parse_args(argv)

    artifacts = persistence.resumable_steps(args.run_dir)
    result = run({"artifacts": artifacts, "run_dir": args.run_dir})
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

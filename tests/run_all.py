"""Run every test module and return a combined exit code.

    python tests/run_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import (  # noqa: E402
    test_cell_calling,
    test_cellranger_count,
    test_count_matrix_classify,
    test_fastq_preflight,
    test_fastq_qc,
    test_fastq_whitelist,
    test_graph_smoke,
    test_ingest_validate,
    test_matrix_preflight,
    test_merge_samples,
    test_resolve_reference,
    test_post_load_validate,
    test_apply_cell_qc_filter,
    test_detect_doublets,
    test_normalize_hvg_prepare,
    test_run_pca,
    test_run_integration,
    test_run_clustering,
    test_run_umap,
    test_find_markers,
    test_annotate_cells,
    test_cross_check_annotation,
    test_provenance,
    test_registry_docs,
    test_build_report,
    test_persistence,
    test_resume,
    test_terminal_status,
    test_resume_validation,
    test_durable_resume,
    test_revision,
    test_human_review_decision,
    test_sample_qc_triage,
    test_nodes,
    test_cli_env,
    test_judge,
    test_judge_provenance,
    test_step_prompts,
    test_run_qc_metrics,
)

#: Roughly pipeline order. Each module appears once — a repeat costs the time
#: twice and inflates the totals printed at the end.
MODULES = (
    test_ingest_validate,
    test_resolve_reference,
    test_matrix_preflight,
    test_fastq_preflight,
    test_fastq_qc,
    test_fastq_whitelist,
    test_cellranger_count,
    test_count_matrix_classify,
    test_cell_calling,
    test_merge_samples,
    test_post_load_validate,
    test_run_qc_metrics,
    test_apply_cell_qc_filter,
    test_detect_doublets,
    test_normalize_hvg_prepare,
    test_run_pca,
    test_run_integration,
    test_run_clustering,
    test_run_umap,
    test_find_markers,
    test_annotate_cells,
    test_cross_check_annotation,
    test_provenance,
    test_registry_docs,
    test_build_report,
    test_persistence,
    test_resume,
    test_terminal_status,
    test_resume_validation,
    test_durable_resume,
    test_revision,
    test_human_review_decision,
    test_sample_qc_triage,
    test_nodes,
    test_cli_env,
    test_judge,
    test_judge_provenance,
    test_step_prompts,
    test_graph_smoke,
)


def main() -> int:
    failed = 0
    for module in MODULES:
        print(f"\n{module.__name__}")
        failed |= module.main()
    print("\nFAILURES" if failed else "\nall suites passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

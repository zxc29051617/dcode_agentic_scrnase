# Skills

This folder holds the step-level tool/skill scaffolds for the scRNA-seq workflow.

## Current scaffolded tools

### Intake and routing
- `ingest_validate`
- `sample_qc_triage`
- `fastq_preflight`
- `cellranger_count`
- `count_matrix_classify`

### Count-matrix and analysis tools
- `load_raw_counts`
- `load_filtered_counts`
- `cell_calling_review`
- `run_qc_metrics`
- `apply_cell_qc_filter`
- `detect_doublets`
- `normalize_hvg_prepare`
- `run_pca`
- `run_integration`
- `run_clustering`
- `run_umap`
- `find_markers`
- `annotate_cells`

### Human gate and reporting
- `human_review_decision`
- `build_report`

### Judge tools
- `judge_ingest`
- `judge_sample_qc`
- `judge_fastq_preflight`
- `judge_cellranger_count`
- `judge_matrix_classify`
- `judge_raw_counts`
- `judge_filtered_counts`
- `judge_cell_calling`
- `judge_qc`
- `judge_cell_qc_filter`
- `judge_doublets`
- `judge_preprocess`
- `judge_pca`
- `judge_integration`
- `judge_clustering`
- `judge_umap`
- `judge_markers`
- `judge_annotation`
- `judge_report`

## Layout
Each skill gets its own folder:
- `SKILL.md` — contract, inputs, outputs, failure modes, and orchestration role
- `<skill_name>.py` — deterministic implementation scaffold

## Rule
The workflow orchestrator should call these skills through MCP or another tool gateway instead of hardcoding the analysis logic into one monolith.

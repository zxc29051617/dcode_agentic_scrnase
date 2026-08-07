"""Generate the skill scaffolds.

Existing files are never overwritten: once a skill has a real implementation,
re-running this script must not wipe it. Pass --force to regenerate anyway.

All 25 registry steps are implemented, so in practice this script has nothing
left to create — it is kept for adding a new step. It does not generate
`judge_*` folders: see the note beside `judge_specs` below.

The specs here are frozen at the shape each skill was scaffolded with, not what
it grew into. `src/registry.py` is the list of steps; `skills/<name>/SKILL.md`
is what a step actually does.
"""

import sys
from pathlib import Path
from textwrap import dedent

BASE = Path(__file__).resolve().parent / 'skills'
FORCE = '--force' in sys.argv


def write(path, content):
    """Write unless the file exists, so implemented skills survive a re-run."""
    if path.exists() and not FORCE:
        print(f'skip (exists): {path.relative_to(BASE.parent)}')
        return
    path.write_text(content, encoding='utf-8')

analysis_specs = [
    {
        'name': 'ingest_validate',
        'description': 'Identify whether the input bundle is FASTQ, raw matrix, filtered matrix, or h5ad and normalize the state for routing.',
        'inputs': ['input bundle', 'optional sample metadata', 'routing config'],
        'outputs': ['normalized state', 'detected input type', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Validate the incoming bundle shape and basic file presence.', 'Classify the entry point for downstream routing.', 'Refuse ambiguous bundles that need human clarification.'],
        'failures': ['Unreadable or missing inputs', 'Mixed assay types in one bundle', 'Unsupported or inconsistent bundle layout'],
        'downstream': 'sample_qc_triage, fastq_preflight, or count_matrix_classify',
    },
    {
        'name': 'sample_qc_triage',
        'description': 'Perform deterministic sample-level QC triage from a summary metrics table before the main workflow branches.',
        'inputs': ['QC metrics CSV', 'optional identity checks', 'triage policy'],
        'outputs': ['sample_flags', 'summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Validate the sample metrics table shape and required columns.', 'Flag sample-level outliers and identity inconsistencies.', 'Keep the triage operational rather than clinical.'],
        'failures': ['Missing required QC columns', 'No sample rows present', 'Conflicting or malformed identity fields'],
        'downstream': 'FASTQ or matrix route',
    },
    {
        'name': 'load_raw_counts',
        'description': 'Load raw count matrices or raw-count h5ad inputs into AnnData while preserving the pre-cell-calling state.',
        'inputs': ['raw matrix bundle', 'optional source hint', 'load config'],
        'outputs': ['adata', 'source_state', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Import raw counts without collapsing the pre-cell-calling evidence.', 'Record source provenance and matrix shape metadata.', 'Force a cell-calling review if the state is still unresolved.'],
        'failures': ['Missing raw matrix files', 'Unsupported h5ad state', 'Evidence inconsistent with raw-count assumptions'],
        'downstream': 'cell_calling_review or mainline QC',
    },
    {
        'name': 'load_filtered_counts',
        'description': 'Load filtered count matrices or filtered-count h5ad inputs into AnnData for the downstream mainline.',
        'inputs': ['filtered matrix bundle', 'optional source hint', 'load config'],
        'outputs': ['adata', 'source_state', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Import filtered counts and preserve source provenance.', 'Treat the input as post-cell-calling unless evidence says otherwise.', 'Pass a structured state forward for the core analysis line.'],
        'failures': ['Missing filtered matrix files', 'Unsupported h5ad state', 'Evidence that conflicts with filtered-count assumptions'],
        'downstream': 'mainline QC',
    },
    {
        'name': 'cell_calling_review',
        'description': 'Review raw-matrix evidence and decide whether cell calling is already resolved or needs human attention.',
        'inputs': ['raw matrix summary', 'source state', 'review policy'],
        'outputs': ['cell_calling_state', 'evidence', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Inspect barcodes, counts, and matrix structure for cell-calling clues.', 'Emit a decision payload rather than silently assuming resolution.', 'Escalate ambiguous cases to human review.'],
        'failures': ['No usable raw-matrix summary', 'Ambiguous evidence with no reliable decision', 'Missing provenance needed for review'],
        'downstream': 'mainline or human gate',
    },
    {
        'name': 'run_qc_metrics',
        'description': 'Compute deterministic QC metrics from AnnData as the first analytical step on the mainline.',
        'inputs': ['AnnData', 'QC config'],
        'outputs': ['qc_metrics', 'qc_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Calculate standard cell- and gene-level QC summaries.', 'Keep raw observations intact for later filtering decisions.', 'Provide a compact summary for the judge node.'],
        'failures': ['Invalid AnnData input', 'Missing QC-relevant annotations', 'Matrix dimensions cannot be interpreted'],
        'downstream': 'cell QC filter',
    },
    {
        'name': 'apply_cell_qc_filter',
        'description': 'Apply QC thresholds to AnnData and produce a filtered object for the downstream steps.',
        'inputs': ['AnnData', 'thresholds', 'filter policy'],
        'outputs': ['filtered_adata', 'filter_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Apply explicit thresholds without hiding the removed-cell burden.', 'Preserve counts of retained and removed cells.', 'Return a filtered AnnData ready for doublet detection.'],
        'failures': ['Threshold schema mismatch', 'Invalid AnnData object', 'Filtering would remove all cells'],
        'downstream': 'doublet detection',
    },
    {
        'name': 'detect_doublets',
        'description': 'Detect and annotate likely doublets before normalization and dimensionality reduction.',
        'inputs': ['AnnData', 'doublet config'],
        'outputs': ['doublet_calls', 'filtered_adata', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Run a deterministic doublet-calling stage or pass through a documented placeholder.', 'Preserve the filtered object after doublet handling.', 'Record the doublet burden for the judge.'],
        'failures': ['Invalid input AnnData', 'Missing doublet configuration', 'Incompatible per-cell annotations'],
        'downstream': 'preprocess',
    },
    {
        'name': 'normalize_hvg_prepare',
        'description': 'Normalize counts, log-transform them, select HVGs, and prepare a PCA-ready AnnData object.',
        'inputs': ['AnnData', 'normalization config'],
        'outputs': ['normalized_adata', 'hvgs', 'prep_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Normalize and transform the expression matrix in a reproducible way.', 'Select HVGs according to the configured policy.', 'Prepare a matrix suitable for PCA and later steps.'],
        'failures': ['Missing normalized input', 'No valid genes left after filtering', 'Configuration cannot support HVG selection'],
        'downstream': 'PCA',
    },
    {
        'name': 'run_pca',
        'description': 'Compute PCA embeddings and loadings from the prepared AnnData object.',
        'inputs': ['AnnData', 'PCA config'],
        'outputs': ['pca_embedding', 'loadings', 'variance_explained', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Fit PCA using the configured number of components.', 'Store the embedding and loadings in a structured form.', 'Summarize variance explained for judge review.'],
        'failures': ['Invalid PCA input', 'Too few variable features', 'Component count exceeds matrix rank'],
        'downstream': 'integration or clustering',
    },
    {
        'name': 'run_integration',
        'description': 'Perform batch correction or latent integration when multiple batches are present.',
        'inputs': ['AnnData', 'integration config'],
        'outputs': ['integrated_embedding', 'integration_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Use the configured integration strategy only when it is scientifically justified.', 'Retain provenance for the integrated representation.', 'Report whether the data actually warranted integration.'],
        'failures': ['No usable batches or covariates', 'Integration config is incompatible with the input', 'Latent representation cannot be constructed'],
        'downstream': 'clustering',
    },
    {
        'name': 'run_clustering',
        'description': 'Assign cluster labels to the integrated or PCA-based representation.',
        'inputs': ['AnnData', 'clustering config'],
        'outputs': ['cluster_labels', 'clustering_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Run a documented clustering strategy such as Leiden.', 'Record cluster assignments with provenance.', 'Expose cluster size and quality summaries to the judge.'],
        'failures': ['Missing graph or embedding', 'Invalid clustering config', 'No clusters can be formed'],
        'downstream': 'UMAP',
    },
    {
        'name': 'run_umap',
        'description': 'Compute UMAP coordinates for visual inspection after clustering.',
        'inputs': ['AnnData', 'UMAP config'],
        'outputs': ['umap_coordinates', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Create 2D coordinates from the selected embedding.', 'Preserve the mapping between cells and coordinates.', 'Keep the step deterministic and reproducible.'],
        'failures': ['Missing embedding or neighbor graph', 'UMAP parameters are invalid', 'Coordinates cannot be constructed'],
        'downstream': 'markers',
    },
    {
        'name': 'find_markers',
        'description': 'Compute cluster marker tables from the clustered AnnData object.',
        'inputs': ['AnnData', 'cluster labels', 'marker config'],
        'outputs': ['marker_table', 'marker_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Run marker discovery on the defined cluster labels.', 'Summarize the strongest marker evidence per cluster.', 'Pass a table suitable for the annotation step.'],
        'failures': ['Missing cluster labels', 'AnnData lacks marker-relevant layers', 'No meaningful differential signal'],
        'downstream': 'annotation',
    },
    {
        'name': 'annotate_cells',
        'description': 'Assign provisional cell labels from marker evidence and any approved reference evidence.',
        'inputs': ['marker table', 'reference evidence', 'annotation policy'],
        'outputs': ['labels', 'confidence', 'evidence', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Map marker evidence to provisional biological labels.', 'Report confidence and keep unknown or mixed states explicit.', 'Do not force a label when evidence is weak.'],
        'failures': ['Marker table is incomplete', 'Reference evidence conflicts with marker evidence', 'No defensible label can be assigned'],
        'downstream': 'human review',
    },
    {
        'name': 'human_review_decision',
        'description': 'Convert a human decision into a structured accept, revise, or stop action.',
        'inputs': ['judge payload', 'candidate labels', 'decision context'],
        'outputs': ['decision', 'rationale', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Record explicit human review choices.', 'Allow revise or stop without hiding the decision.', 'Feed the chosen action back into the workflow state.'],
        'failures': ['Missing decision context', 'Conflicting review instructions', 'Decision payload cannot be serialized'],
        'downstream': 'report or reroute',
    },
    {
        'name': 'build_report',
        'description': 'Build the final HTML, PDF, and JSON summary from the verified workflow state and artifacts.',
        'inputs': ['final state', 'artifacts', 'report config'],
        'outputs': ['html_report', 'pdf_snapshot', 'json_summary', 'warnings', 'errors', 'recommended_next_tool'],
        'behavior': ['Render the canonical interactive HTML report.', 'Produce a frozen PDF snapshot from the same verified content.', 'Package a machine-readable JSON summary for provenance.'],
        'failures': ['Missing final workflow state', 'Required artifacts are absent', 'Report generation cannot complete cleanly'],
        'downstream': 'done',
    },
]

judge_specs = [
    ('judge_ingest', 'ingest_validate'),
    ('judge_sample_qc', 'sample_qc_triage'),
    ('judge_fastq_preflight', 'fastq_preflight'),
    ('judge_cellranger_count', 'cellranger_count'),
    ('judge_matrix_classify', 'count_matrix_classify'),
    ('judge_raw_counts', 'load_raw_counts'),
    ('judge_filtered_counts', 'load_filtered_counts'),
    ('judge_cell_calling', 'cell_calling_review'),
    ('judge_qc', 'run_qc_metrics'),
    ('judge_cell_qc_filter', 'apply_cell_qc_filter'),
    ('judge_doublets', 'detect_doublets'),
    ('judge_preprocess', 'normalize_hvg_prepare'),
    ('judge_pca', 'run_pca'),
    ('judge_integration', 'run_integration'),
    ('judge_clustering', 'run_clustering'),
    ('judge_umap', 'run_umap'),
    ('judge_markers', 'find_markers'),
    ('judge_annotation', 'annotate_cells'),
    ('judge_report', 'build_report'),
]


def render_lines(items):
    return '\n'.join(f'- {item}' for item in items)


def render_skill_md(spec):
    return (
        "---\n"
        f"name: {spec['name']}\n"
        f"description: {spec['description']}\n"
        "version: 0.1.0\n"
        "---\n\n"
        f"# {spec['name']}\n\n"
        "## Purpose\n"
        f"{spec['description']}\n\n"
        "## Input\n"
        f"{render_lines(spec['inputs'])}\n\n"
        "## Output\n"
        f"{render_lines(spec['outputs'])}\n\n"
        "## Behavior\n"
        f"{render_lines(spec['behavior'])}\n\n"
        "## Failure modes\n"
        f"{render_lines(spec['failures'])}\n\n"
        "## Downstream routing\n"
        f"{spec['downstream']}\n"
    )


def render_judge_md(name, step_name):
    description = f'Judge the result of {step_name} with the shared local pass/warn/fail contract.'
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 0.1.0\n"
        "---\n\n"
        f"# {name}\n\n"
        "## Purpose\n"
        f"Evaluate the output of `{step_name}` and return a structured judge result.\n\n"
        "## Input\n"
        "- step name\n"
        "- analysis result payload\n"
        "- key metrics and artifacts\n"
        "- policy context\n\n"
        "## Output\n"
        "- `step`\n"
        "- `verdict`\n"
        "- `score`\n"
        "- `reasons`\n"
        "- `evidence`\n"
        "- `suggested_action`\n"
        "- `needs_human_review`\n\n"
        "## Behavior\n"
        "- Return JSON only and do not change any analysis outputs.\n"
        "- Apply the shared judge contract from `schemas/judge_result.schema.json`.\n"
        "- Distinguish acceptable results from warning and failure states.\n\n"
        "## Failure modes\n"
        "- Missing step context\n"
        "- Unsupported or incomplete evidence\n"
        "- Output that cannot be assessed against the judge schema\n\n"
        "## Downstream routing\n"
        "pass -> next workflow step; warn/fail -> human review or reroute\n"
    )


def render_py_stub(tool_name, input_fields, output_fields):
    input_lines = ',\n'.join(f'    "{field}"' for field in input_fields)
    output_lines = ',\n'.join(f'    "{field}"' for field in output_fields)
    return (
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        f"TOOL_NAME = \"{tool_name}\"\n"
        "INPUT_FIELDS = (\n"
        f"{input_lines}\n"
        "    )\n"
        "OUTPUT_FIELDS = (\n"
        f"{output_lines}\n"
        "    )\n\n\n"
        "def run(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    raise NotImplementedError(f\"{TOOL_NAME} is a scaffold\")\n\n\n"
        "def main() -> int:\n"
        "    raise NotImplementedError(f\"{TOOL_NAME} CLI is a scaffold\")\n"
    )


for spec in analysis_specs:
    folder = BASE / spec['name']
    folder.mkdir(parents=True, exist_ok=True)
    write(folder / 'SKILL.md', render_skill_md(spec))
    write(folder / f"{spec['name']}.py", render_py_stub(spec['name'], spec['inputs'], spec['outputs']))

# Judges are deliberately not generated. An early design gave each step its own
# `judge_*` tool; the implementation went with one shared contract in
# `src/judge.py` instead, and the 19 generated folders sat there raising
# NotImplementedError until they were deleted. Regenerating them here would put
# them straight back. `judge_specs` is kept only because the registry's `judge`
# field names those graph nodes.

readme = dedent('''\
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
''')
(BASE / 'README.md').write_text(readme, encoding='utf-8')
print('generated')


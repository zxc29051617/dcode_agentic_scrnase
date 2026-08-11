# Skills

One folder per pipeline step. Each holds the contract and the implementation:

```
skills/<name>/
  SKILL.md     what it takes, what it returns, how it fails, what it is for
  <name>.py    run(payload) -> dict, and a standalone CLI
```

`src/registry.py` is the source of truth for which steps exist. The inventory
below is generated from it — see [Keeping this honest](#keeping-this-honest).

## How the orchestrator calls them

Directly. `src/registry.py`'s `call_skill(name, payload)` imports
`skills/<name>/<name>.py` and calls its `run(payload)`. There is no MCP server,
no RPC and no subprocess; the contract is the function signature.

That is also why every step is a CLI in its own right:

```bash
python skills/run_pca/run_pca.py --help
```

A step is added by writing the folder and one `StepSpec` in `src/registry.py`.
`graph.py` has to be edited too — it is where the step's edges live.

## What a skill may and may not do

- It returns a dict. Anything large — an AnnData, a marker table — is written to
  disk and returned as a path, because graph state has to stay serialisable.
- It never imports `langgraph`, never reads `WorkflowState`, and never decides
  routing. It reports; the graph routes.
- It may leave a choice to a person by returning a `*_state` of `needs_review`
  rather than guessing. Four steps do.

<!-- BEGIN GENERATED skill-list — python scripts/export_registry_docs.py -->

**26 skills**, one per registry step, in pipeline order. Counted from `src/registry.py`, never typed.

### utility — intake, validation and reporting

- [`ingest_validate`](ingest_validate/SKILL.md)
- [`sample_qc_triage`](sample_qc_triage/SKILL.md)
- [`resolve_reference`](resolve_reference/SKILL.md)
- [`matrix_preflight`](matrix_preflight/SKILL.md)
- [`build_report`](build_report/SKILL.md)

### upstream — FASTQ to counts

- [`fastq_preflight`](fastq_preflight/SKILL.md)
- [`fastq_qc`](fastq_qc/SKILL.md)
- [`cellranger_count`](cellranger_count/SKILL.md)

### router — chooses between routes

- [`count_matrix_classify`](count_matrix_classify/SKILL.md)

### analysis — the count matrix and everything after it

- [`load_raw_counts`](load_raw_counts/SKILL.md)
- [`load_filtered_counts`](load_filtered_counts/SKILL.md)
- [`cell_calling_review`](cell_calling_review/SKILL.md)
- [`merge_samples`](merge_samples/SKILL.md)
- [`post_load_validate`](post_load_validate/SKILL.md)
- [`run_qc_metrics`](run_qc_metrics/SKILL.md)
- [`apply_cell_qc_filter`](apply_cell_qc_filter/SKILL.md)
- [`detect_doublets`](detect_doublets/SKILL.md)
- [`normalize_hvg_prepare`](normalize_hvg_prepare/SKILL.md)
- [`run_pca`](run_pca/SKILL.md)
- [`run_integration`](run_integration/SKILL.md)
- [`run_clustering`](run_clustering/SKILL.md)
- [`run_umap`](run_umap/SKILL.md)
- [`find_markers`](find_markers/SKILL.md)
- [`annotate_cells`](annotate_cells/SKILL.md)
- [`cross_check_annotation`](cross_check_annotation/SKILL.md)

### gate — a person decides

- [`human_review_decision`](human_review_decision/SKILL.md)

<!-- END GENERATED skill-list -->

## There are no judge skills

There is no `skills/judge_*`. Judging is one implementation in `src/judge.py`
with one `JudgeResult` contract, and each step hands it a different payload.
`REGISTRY[step].judge` is the **name of a node in the graph and a label in the
audit log** — `judge_qc`, `judge_pca` — not a module path and not a folder.

This README used to list nineteen of them. They were a design that was
considered and dropped, the folders were deleted, and the list stayed.

## Keeping this honest

The inventory above is written by `scripts/export_registry_docs.py` from
`src/registry.py`, and CI runs it with `--check`. Editing it by hand will be
overwritten; add the step to the registry instead.

```bash
python scripts/export_registry_docs.py           # rewrite the generated sections
python scripts/export_registry_docs.py --check   # fail if they are stale
```

`tests/test_registry_docs.py` covers the same ground from the other side: every
registry step has a folder, no folder is listed that does not exist, and the
generator is deterministic.

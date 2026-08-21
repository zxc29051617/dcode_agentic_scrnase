# results/

Finished analyses, kept by hand. **Not** where the pipeline writes.

A run writes everything to `runs/<run_id>/`, which is in `.gitignore` and is
disposable — each step saves its own `adata.h5ad` so a run can be resumed from
disk, which is why one run costs about 410 MB. Copy what is worth keeping in
here, then delete the run.

```
results/
  pbmc-2samples_2026-08-07/
    build_report/report.html    the report, figures inlined — open this one
    build_report/report.md      the same thing as text
    build_report/figures/       12 PNGs
    cross_check_annotation/     CellTypist vs the marker database, per cluster
    markers.csv                 every cluster's ranked genes
    audit.jsonl                 every step, verdict and gate decision, in order
    run_metadata.json           run id, git commit, seeds, package versions
```

## The folder names differ from the run ids, on purpose

The pipeline names a run `20260807T133021Z-3e970fc3` — a UTC timestamp plus
eight random characters (`src/state.py:94`). That sorts correctly and cannot
collide when two runs start in the same second, which is what it is for.

It also tells a reader nothing. Folders in here are named for the analysis
instead. The original run id is still in `run_metadata.json`, so renaming
loses nothing.

## pbmc-2samples_2026-08-07

`pbmc_1k_v2` + `pbmc_1k_v3`, merged: 2,159 cells, 15 clusters, 13 cell types.

Five steps drew a `warn` and would each have stopped for a person, had the run
not been given `--headless-decision accept`:

| step | score | what it said |
|---|---|---|
| run_qc_metrics | 80 | the two libraries differ in mitochondrial load (3.13% vs 6.92%), so one global threshold may not suit both |
| detect_doublets | 80 | 19 doublets found and not removed; waiting on a decision |
| run_clustering | 78 | 15 clusters, the smallest only 11 cells |
| annotate_cells | 85 | clusters 4 and 8 have per-cell consensus below 70% |
| cross_check_annotation | 60 | CellTypist and the marker database name different cell types for clusters 0, 1 and 6 |

The last one is worth reading. CellTypist calls clusters 0 and 1 classical
monocytes at 1.00 and 0.99 confidence; the marker database calls both
`Neutrophil`. A Ficoll gradient leaves granulocytes in the pellet, so
neutrophils are not in a PBMC preparation — the database is wrong, and it is
wrong for a recoverable reason: it gives Neutrophil 84 markers against CD14
Monocyte's 12, so pan-myeloid genes that both cell types express are counted
for only one of them.

Reproduce it with the command in `run_metadata.json`, under `source.command`.

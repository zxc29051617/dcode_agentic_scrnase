---
name: run_integration
description: Batch-correct the PCA embedding with Harmony, only when there is a batch to correct against.
version: 0.2.0
status: implemented
---

# run_integration

## Purpose
Correct `X_pca` for library-of-origin effects, producing `X_pca_harmony`, so
downstream clustering groups cells by biology rather than by which sample they
came from.

## The first mainline step that decides *whether*, not just *how*
Every earlier judgment-call step (`apply_cell_qc_filter`, `cell_calling_review`)
stops because the value needed is something only the operator can supply. This
step is the opposite shape: whether a single library needs correcting against
itself is not a judgment call, it is a fact readable straight off the object.
So it never stops — it reads `obs["sample"]` and decides.

- **One sample, or no `sample` column** → skip. Running Harmony on a single
  library would not error, but it would spend time correcting an embedding
  against noise and call the result "integrated" when nothing was integrated.
  `X_pca` is left as the embedding downstream steps should read.
- **More than one sample, all with enough cells** → run Harmony.
- **Some samples too small to correct reliably** (`MIN_CELLS_PER_BATCH = 20`)
  → skip, with a warning naming which samples and how many cells. Harmony fits
  a per-batch clustering step internally; a batch of a handful of cells cannot
  support that fit, and reporting a number nobody should trust is worse than
  skipping.

`force_integration` overrides all three checks, for the rare case an operator
wants Harmony to run anyway.

## The embedding is corrected, not the expression matrix
Harmony writes `obsm["X_pca_harmony"]`; `X` and `layers["counts"]` are never
touched. `find_markers` and any other step reading expression works on the
same values it always would — only `run_clustering` and `run_umap` are meant
to read `X_pca_harmony`.

## Calls `harmonypy` directly — a real cross-version bug, not overengineering
`scanpy.external.pp.harmony_integrate` does `harmony_out.Z_corr.T`
unconditionally, assuming `Z_corr` comes back shaped `(n_pcs, n_obs)`. That was
true in `harmonypy` 0.0.10, which the wrapper was written against, but every
release since — 0.1.0 through the current 2.0.0 — returns `(n_obs, n_pcs)`
instead. The transpose then produces the wrong orientation, and assigning it
into `obsm` fails a shape check that has nothing to do with the data:

```
ValueError: Value passed for key 'X_pca_harmony' is of incorrect shape.
```

Reproduced on this project's environment with `harmonypy` 2.0.0, 0.2.0, and
0.1.0 — none matched the wrapper's assumption; only 0.0.10 did. Rather than
pin an increasingly old dependency to match scanpy's assumption,
`_run_harmony()` calls `harmonypy.run_harmony` itself and checks which axis of
the returned array actually matches the cell count, orienting accordingly.
Correct against whichever version is installed, not just the one the wrapper
happened to be written for.

## What it reports

| key | meaning |
|---|---|
| `integration_summary.integrated` | whether Harmony actually ran |
| `integration_summary.embedding_key` | `X_pca_harmony` if integrated, else `X_pca` — what downstream steps should read |
| `integration_summary.n_batches`, `.batch_sizes` | what was found in `obs[batch_key]` |

## Failure modes
- no AnnData (`run_pca` did not run)
- the path does not exist
- no `obsm["X_pca"]` (`run_pca` did not run)
- `force_integration` requested but the batch key is absent
- Harmony itself errors, or returns an array matching neither axis to the cell count

## Downstream routing
`run_clustering`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object post-PCA (2,159 cells, 2
batches — 1,149 and 1,010 cells): integrated cleanly, `X_pca_harmony` shaped
`(2159, 50)`, `X` and `layers["counts"]` unchanged.

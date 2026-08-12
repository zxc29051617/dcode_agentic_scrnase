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

## Correcting is opt-in, and only ever on a declared technical batch
This step used to default `batch_key` to `"sample"` and correct whenever that
column held two or more values. `obs["sample"]` is the library name, and the
library name came from a FASTQ filename — so a run of three disease and three
control libraries was "integrated" on the difference between disease and
control, and the warning list came back empty. Harmony did exactly what it was
asked; it was asked the wrong question.

A library is not a technical batch. Libraries usually differ by donor and
condition as well, and those are differences to keep.

**`--integration-mode` decides, and has no default.** Unset is a third state,
distinct from `none`, and is recorded as such (`mode_source`):

| mode | libraries | what happens |
|---|---|---|
| unset | one | skip, note, no warning — nothing to correct |
| unset | several | skip, **warning** naming them, and the gate asks. `accept` takes the uncorrected `X_pca`; `revise` may set `integration_mode` |
| `none` | any | skip, recorded as the operator's decision |
| `harmony` | any | correct on `obs["technical_batch"]`, subject to the checks below |

**`harmony` requires a validated `--sample-manifest`.** Without
`obs["technical_batch"]` it fails closed. Nothing stands in for a declared
batch — not `sample`, `library_id`, `sample_id`, `donor_id` or `condition` —
and passing `--batch-key` naming any of them is refused rather than honoured.

- **One value in `technical_batch`** → skip with a note. One batch corrected
  against itself has no meaning, and this holds under `force_integration` too.
- **A batch below `MIN_CELLS_PER_BATCH = 20`** → skip with a warning naming it.
  Harmony fits a per-batch clustering step internally and a handful of cells
  cannot support it.

## Confounding is decided structurally, not by a threshold
`condition` and `technical_batch` are put on opposite sides of a bipartite
graph, joined wherever a library has both. If that graph is **connected**, some
batch holds more than one condition, so a batch difference can be told apart
from a condition difference. If it falls into **separate components**, no
comparison bridges them: the two effects enter the data identically, and
removing the batch removes the condition.

That is a count of components, not a coefficient. There is no cutoff to tune —
a tuned cutoff would be a judgement about somebody's experiment presented as a
measurement.

- **Fully confounded** → refuse, and report the contingency table. Harmony
  cannot separate what the design did not separate, and this step does not
  claim otherwise. `force_integration` will run it, and then says plainly in a
  warning that the condition difference is gone from the corrected embedding.
- **Uneven but still connected** → run, with a warning carrying the table. An
  unbalanced-but-estimable design is the operator's call, and no arbitrary
  cutoff should make it quietly.

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
| `integration_summary.n_batches`, `.batch_sizes` | what was found in `obs["technical_batch"]` |
| `integration_summary.integration_mode` | what was asked for: `none`, `harmony`, or `null` |
| `integration_summary.mode_source` | `operator` or `unanswered` — whether anybody asked |
| `integration_summary.confounding` | components, the contingency table, and whether the design is separable. Counts of libraries only; no id appears in it, which is what lets it reach the report |

## Failure modes
- no AnnData (`run_pca` did not run)
- the path does not exist
- no `obsm["X_pca"]` (`run_pca` did not run)
- `integration_mode` is neither `none` nor `harmony`
- `integration_mode=harmony` with no `obs["technical_batch"]` — no validated manifest
- `--batch-key` naming anything other than `technical_batch`
- Harmony itself errors, or returns an array matching neither axis to the cell count

## Downstream routing
`run_clustering`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object post-PCA (2,159 cells, 2
batches — 1,149 and 1,010 cells): integrated cleanly, `X_pca_harmony` shaped
`(2159, 50)`, `X` and `layers["counts"]` unchanged. That run predates
`--integration-mode`; under the current contract the same object needs a
manifest declaring those two libraries' `technical_batch` and an explicit
`--integration-mode harmony`.

The behaviour this replaced was measured before it was changed: six synthetic
libraries, three labelled disease and three control, no design supplied —
`integrated=True`, `batch_key='sample'`, `n_batches=6`, `warnings=[]`.

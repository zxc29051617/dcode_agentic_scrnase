---
name: post_load_validate
description: The merge point — guarantee one shape of AnnData whichever route produced it, and cross-check the genome against the declared species.
version: 0.1.0
status: implemented
---

# post_load_validate

## Purpose
Three steps can hand a matrix to the mainline — `load_filtered_counts`,
`cell_calling_review`, and `load_raw_counts` for a raw matrix someone had
already called cells on. Three producers and one consumer is exactly when the
consumer starts growing per-route special cases, so everything downstream of
here is promised the same object instead.

## What is promised

| | |
|---|---|
| names | `obs_names` and `var_names` unique |
| `X` | raw non-negative integer counts, never something already normalised |
| `layers["counts"]` | the same counts, kept reachable after normalisation overwrites `X` |
| barcodes | every one has at least one count |
| `var` | gene ids alongside symbols, where the source had them |
| genome | recorded, and cross-checked against the declared species |

`normalizations` lists what had to be changed to meet that contract. Empty is
the good case; a non-empty list means a producer upstream emitted something the
mainline could not have used as-is.

## Why the counts layer
`normalize_hvg_prepare` writes normalised values into `X`. Anything afterwards
that needs the originals — differential expression, re-filtering, export — has
nowhere to look unless a copy was put aside first, and by then it is too late.

## A second species check, on the object that actually reaches the mainline
`matrix_preflight` checks the file on the way in and `resolve_reference` checks
the reference on the FASTQ side. This one checks the AnnData that is actually
being handed over, after loading and any cell-calling subset — the last point
where a wrong organism can still be caught.

A 10x `.h5` records its reference in `var['genome']` and scanpy keeps it, so the
check is available for free. An mtx directory records nothing.

| situation | treatment |
|---|---|
| genome matches the declared species | verified |
| genome contradicts it | **error** — every number downstream would be filed under the wrong organism |
| no genome recorded (mtx) | note |
| genome unrecognised (custom build) | note |
| two species (barnyard/PDX) | note |
| genome known, no species declared | warning — actionable, just say what it is |

Being unable to verify is a *note*, not a warning: most public data ships as
mtx, and stopping every such run at the gate teaches people to click through.

## Refusing pre-normalised data
Fractional or negative values mean the matrix has already been log-transformed
or scaled. Feeding that into a counts pipeline produces plots that look fine and
numbers that mean nothing, so it costs one pass over the data to refuse.

## Downstream routing
`run_qc_metrics`.

## Verified against
The real `pbmc_1k_v3` filtered matrix: 1,218 cells, 39,048 genes, genome
`T2T_CHM13v2_RefSeqLiftoff_v5_3` verified as human, zero normalizations needed.
Declared as `mouse`, the same file is refused.

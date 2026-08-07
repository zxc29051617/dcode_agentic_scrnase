---
name: sample_qc_triage
description: Decide which libraries enter the run, before any of them are counted — reporting, never pruning on its own.
version: 0.2.0
status: implemented
---

# sample_qc_triage

## Purpose
An optional pre-route step. It runs after `ingest_validate` and before the
FASTQ/matrix split — the last moment a library can be left out cheaply.
Counting one takes Cell Ranger 20–40 minutes, and a dead library that gets
through is worse than slow: it merges into the analysis and every number
downstream quietly includes it.

Enabled with `sample_qc_triage: true` in config. Off by default, and skipped
entirely when off.

## It never excludes a sample on its own
This pipeline already shipped a bug where a two-sample run analysed one library
and reported it as the whole study. A triage step that dropped samples on its
own judgement would be that bug with a rationale attached.

So it takes `apply_cell_qc_filter`'s shape:

1. **Measure.** Percentiles per numeric column, and which samples each
   candidate bound would exclude.
2. **Flag.** Samples failing the bounds the operator set, as warnings — which
   the judge and the human gate turn into a stop, using the existing machinery.
3. **Exclude, only when told.** `exclude_samples` names them explicitly.

| `triage_state` | meaning |
|---|---|
| `no_action` | nothing flagged, or nothing to assess |
| `needs_review` | samples flagged, **none excluded** |
| `applied` | `exclude_samples` was given and honoured |

## The exclusion actually reaches the work
A triage that recorded an exclusion and then counted the sample anyway would be
the same lie in a different place, so both routes honour it at the one point
each decides what to work on:

- **matrix** — `matrix_preflight` reads `sample_qc_triage.matrix_paths` before
  `ingest_validate.matrix_paths`
- **FASTQ** — `fastq_preflight` drops excluded libraries before building its
  library list, and says which

## No default thresholds
Same reasoning as `apply_cell_qc_filter`: there is no universal minimum read
count or saturation, and a number that suits one assay discards good libraries
from another. Bounds live in `config.sample_thresholds`, and therefore in the
audit log.

```python
sample_thresholds = {"mean_reads_per_cell": {"min": 10000},
                     "saturation": {"min": 0.4}}
```

## Operational, not clinical
It asks whether a library can be analysed, not whether its biology is
interesting. The structural checks are the ones worth having:

- **a duplicated sample name is an error** — two rows under one identity is how
  two libraries become one
- **a library in the run but absent from the table** is named, not skipped
  quietly
- **a table row that is not a library in this run** is named too: the table may
  describe a different experiment
- **`exclude_samples` naming something unknown is an error**, so a typo cannot
  look like a successful exclusion
- **excluding everything is refused**

## Input
A CSV with one row per library (`config.qc_metrics_csv`), or the
`sample_metadata` state carries. The sample column is found from the usual
names (`sample`, `library_id`, …) or set with `config.sample_column`.

Enabled with no table at all produces a **warning**, not silence — the operator
believes triage happened.

## Failure modes
- the metrics table cannot be read, or does not exist
- no column can be identified as the sample name
- a sample appears more than once
- `exclude_samples` names something that is not in the run or the table
- excluding would leave nothing to analyse

## Downstream routing
The FASTQ or matrix route, as `ingest_validate` determined. Triage changes
*which libraries* travel, never *which way* they go.

## Verified against
The real `pbmc_1k_v2` + `pbmc_1k_v3` pair, with a metrics table marking v3 as
shallow (1,200 reads/cell, 0.10 saturation):

| run | result |
|---|---|
| bound `mean_reads_per_cell >= 10000`, nothing excluded | `needs_review`, v3 flagged, **both libraries still analysed** |
| `exclude_samples: ["pbmc_1k_v3"]` | `applied`, and the report reads 1,010 cells · 1 sample · 10 clusters · 8 cell types |

The second is the check that matters: the exclusion survived every step and
reached the report, rather than being recorded and ignored.

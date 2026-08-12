# The study design, and why it is not the library name

## What went wrong

`ingest_validate` read a library name out of a FASTQ filename with a regex.
`cellranger_count` reused it as `library_id`. `merge_samples` wrote it into
`obs["sample"]`. `run_integration` defaulted `batch_key` to `"sample"` and
corrected on it whenever it held two or more values, each with at least 20
cells.

So five different things were one string, and that string came from however the
files happened to be named:

| question | what answered it |
|---|---|
| which sequencing library? | the filename |
| which biological specimen? | the filename |
| which subject? | the filename |
| which experimental group? | the filename |
| which technical batch, safe to remove? | the filename |

Measured on the tree before this change: six synthetic libraries, three labelled
disease and three control, no design information anywhere in the object or the
config. Result — `integrated=True`, `batch_key='sample'`, `n_batches=6`,
`warnings=[]`. Harmony removed the difference between disease and control, and
nothing in the run said so. The output looked entirely normal.

That is the same failure mode as the three bugs fixed in the previous round: not
a crash, not an error, a confident wrong answer.

## The contract

One row per sequencing library. Five required columns, a documented set of
optional ones, and a strict whitelist — an unrecognised column is refused rather
than carried.

```csv
library_id,sample_id,donor_id,condition,technical_batch
LIB001,S001,D001,control,BATCH_A
LIB002,S002,D002,disease,BATCH_A
LIB003,S003,D003,control,BATCH_B
LIB004,S004,D004,disease,BATCH_B
```

| column | means | may be a batch key? |
|---|---|---|
| `library_id` | one build/capture/sequencing unit. Must match what the run found, exactly | no — it is the identity |
| `sample_id` | biological specimen. One specimen may be several libraries | no |
| `donor_id` | pseudonymous subject code. One subject may give several specimens | no |
| `condition` | the biological difference under study | **never** |
| `technical_batch` | how the library was built, captured or sequenced | **the only one** |

Optional and documented: `tissue`, `timepoint`, `treatment`, `sex`,
`biological_replicate`. All biological. `timepoint` in particular often lines up
with when a library was built — that is exactly the confusion the confounding
check exists to surface, and it can only surface if the two stay separate
columns.

### Refusals

- `library_id` must be unique, and must match the libraries found one-to-one in
  both directions. Missing rows, extra rows, and near-matches all fail closed.
- Matching is exact string equality. No case folding, no prefix matching, no
  edit distance. A manifest that nearly matches is one that has not been checked.
- Row order and library order change nothing: the manifest is sorted before it
  is hashed.
- A blank cell is unknown. It is never filled from the row above, from the
  majority, or from the filename.
- Values must be pseudonymous codes — `^[A-Za-z0-9_.-]{1,64}$`. Anything with a
  space is refused, as are values shaped like dates, identity-card numbers and
  long digit runs, whichever column they arrive in.
- Columns named for direct identifiers (`patient_name`, `name`, `mrn`,
  `medical_record_number`, and others) are refused by name, with an error that
  says why rather than only that the column is unknown.
- Errors never quote the offending value back, since the error text travels.

## Integration

`--integration-mode` has no default. Unset is a third state, distinct from
`none`, and provenance records which it was.

| mode | libraries | behaviour |
|---|---|---|
| unset | one | skip, note, no warning |
| unset | several | skip, warning naming them, gate asks. `accept` takes uncorrected `X_pca`; `revise` may set `integration_mode` |
| `none` | any | skip, recorded as the operator's decision |
| `harmony` | any | correct on `obs["technical_batch"]` only |

`harmony` without a validated manifest fails closed. Nothing substitutes for a
declared batch, and `--batch-key` naming `sample`, `library_id`, `sample_id`,
`donor_id` or `condition` is refused rather than honoured.

There is deliberately no `auto` mode. Validating that a manifest is *well-formed*
does not establish that `technical_batch` was filled in with technical batches —
putting `donor_id` there is a common misunderstanding, and an `auto` mode would
see several values, no confounding, and quietly remove every individual
difference.

### Confounding, structurally

Put each condition and each batch on opposite sides of a bipartite graph, joined
wherever some library has both.

```
separable (1 component)          fully confounded (2 components)

  control ──┬── BATCH_A            control ──── BATCH_A
            │
  disease ──┴── BATCH_B            disease ──── BATCH_B
```

Connected means some batch holds more than one condition, so a batch difference
can be compared against a condition difference within it. Disconnected means no
comparison bridges the pieces: the two effects enter the data identically, and
removing one removes the other.

This is a count of components, not a coefficient. There is no threshold to
calibrate — a tuned cutoff would be a judgement about somebody's experiment
presented as a measurement.

- **Fully confounded** → refuse, report the contingency table, and leave the
  choice open for a person (`integration_state="needs_review"`). Harmony cannot
  separate what the design did not separate, and this pipeline does not claim it
  can. **`force_integration` does not override this**, unlike the batch-size
  check: accepting a shaky estimate is a decision someone can make, and waiving
  arithmetic is not. `accept` at the gate means the uncorrected `X_pca`; there is
  deliberately no answer meaning "integrate anyway".
- **Uneven but connected** → run, with a warning carrying the table.

## Which column each step reads

Not mechanically renamed. Each step uses the column its own question calls for.

| step | column | why |
|---|---|---|
| `detect_doublets` | `library_id` | doublets form inside a GEM well, which is a library |
| `apply_cell_qc_filter` | `library_id` | QC distributions are per library |
| `run_qc_metrics` | `library_id` | same |
| `normalize_hvg_prepare` | `library_id` | "was this gene called variable because of how one library was sequenced" — per library, always answerable |
| `run_integration` | `technical_batch` | "which differences are technical and removable" — only a declared batch answers this |

`obs["sample"]` is kept as an alias of `library_id` so objects and code written
before the distinction still work. It means the library and nothing else.

## Privacy

- `AnnData.obs` and `runs/<run_id>/manifest/normalized.csv` hold the pseudonymous
  design. Both stay inside the run directory.
- `run_metadata.json`, the audit log, the console and the report get counts, a
  digest and the column list — never rows. In a study with a handful of subjects
  a donor-to-condition listing identifies people however pseudonymous each code
  in it is.
- The contingency table is counts of libraries, so it can be shown.

## Resume and checkpoint

`manifest_sha256` is a config key on `merge_samples` and `run_integration`. It is
the digest of the *normalized* manifest, so:

- editing the file in place invalidates from `merge_samples` down — that is where
  the design first reaches `obs`
- moving the same content to another path invalidates nothing
- reordering rows or padding a cell with spaces invalidates nothing
- everything above `merge_samples` is per-library work that does not read the
  design, and is reused

`--continue-from` uses the snapshot written at run start, never the original CSV,
so a paused run cannot come back to an edited design and end up describing itself
two ways. Passing `--sample-manifest` alongside `--continue-from` is refused,
with the error pointing at `--resume-from`, which recomputes what depended on it.

## What decides what

Every rule on this page is deterministic Python in `src/manifest.py` and
`skills/run_integration/`. Column validation, one-to-one matching, duplicate and
blank detection, the confounding check, and whether Harmony may run are all
decided there.

The model at the gate reads the de-identified summary — counts, the contingency
table, the warning text — and returns a verdict and advice. It cannot alter the
manifest, the config, the object or the result, and no advice becomes a setting
on its own: an override reaches `config` only through an operator answering
`revise`, and only through the allowlist, which validates `integration_mode`
against the two documented values. A `revise` asking for `harmony` without a
valid manifest still fails closed.

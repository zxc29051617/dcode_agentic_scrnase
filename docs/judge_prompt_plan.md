# Per-step judge prompts

Where each of the 25 judged steps sits on the A / B / C ladder, what a step
prompt has to contain, and the evidence for doing this at all.

## The evidence

One step prompt exists, and it was measured against the real endpoint before
being kept. The question was whether the judge would notice that CellTypist and
the marker database name different cell types for the same cluster — a fact
sitting in plain sight in the payload.

| arm | runs that found it |
|---|---|
| payload as built | **0 / 3** |
| payload with the label pairs promoted to their own field, plus a warning posing the question | **0 / 3** |
| payload as built, plus `prompts/steps/cross_check_annotation.md` | **3 / 3** |

The middle arm is the one that matters: adding *data* changed nothing. In both
failing arms the judge read the payload accurately and quoted the flag counts
back — it never compared the names, because the base prompt asks whether a step
ran soundly and by that measure it had. The instruction is what works.

## The ladder

A, B and C are not competing designs. They are levels, chosen per step, and
every one of them needs the prompt first.

| | what it adds | when a step needs it |
|---|---|---|
| **A** | a step prompt | the rules are clear and the payload holds the answer |
| **B** | a step prompt, and a model chosen for this step | the reasoning is materially harder, or so easy a small model is enough |
| **C** | a step prompt, and a tool the judge may call | the payload cannot contain the answer |

## What a step prompt has to contain

Derived from the one that measurably worked, not from taste. Four parts:

**1. The judging task, stated as a task.** Not "consider the clustering" —
"decide whether the resolution produced clusters that a person would defend".
The base prompt already asks whether the step ran; a step prompt exists to ask
something the base prompt does not.

**2. What the numbers cannot see.** This is the part most likely to be left
out and most likely to matter. Every step reports metrics, every set of metrics
has a blind spot, and the judge has no way to know what it is. For
`cross_check_annotation` the sentence is *the flags count genes and compare
scores, they never read a name, so the clearest disagreements arrive
unflagged* — and the disagreement it was built to catch does indeed arrive
unflagged.

**3. Worked examples from this domain.** Two or three, with the right answer.
Without them the judge applies a generic notion of "unusual" and flags
everything that is merely uncommon.

**4. What earns a `warn`.** Otherwise the score is arbitrary and drifts between
runs of the same input.

## Classification

Preliminary. Steps move down the ladder only when a measurement says the prompt
alone was not enough.

### A — prompt only (16)

| step | what its prompt has to ask |
|---|---|
| `ingest_validate` | does the detected input type match what the paths actually look like |
| `resolve_reference` | is the species claim supported by the reference's own genome list |
| `matrix_preflight` | do gene-ID convention, orientation and species agree with each other |
| `fastq_preflight` | is every library complete, and are the read roles assigned as 10x writes them |
| `count_matrix_classify` | does the raw/filtered call match the barcode count and sparsity |
| `load_raw_counts` | is the barcode-rank curve shaped like a real cell-containing library |
| `load_filtered_counts` | do the dimensions and gene IDs match what preflight promised |
| `merge_samples` | did every sample survive the concatenation, with its label intact |
| `post_load_validate` | are raw counts still in a layer, and does the object match its route |
| `normalize_hvg_prepare` | is the HVG count in a defensible range for this many cells |
| `run_pca` | where is the elbow, and does the requested `n_comps` sit past it |
| `run_integration` | did the batch actually need correcting, and was the right basis chosen |
| `run_umap` | did the embedding run on the basis the pipeline said to use |
| `sample_qc_triage` | are the excluded libraries excluded for a stated, per-library reason |
| `fastq_qc` | which FastQC modules failed, and do they matter for 10x read roles |
| `build_report` | are any sections unavailable, and is each absence explained |

### B — prompt and a model chosen for the step (7)

| step | why the reasoning is different |
|---|---|
| `run_qc_metrics` | trade-offs across several distributions at once, per library |
| `apply_cell_qc_filter` | what a threshold costs is a judgement, not a lookup |
| `detect_doublets` | the observed rate has to be read against the loading it implies |
| `cell_calling_review` | reading a knee from a curve, where the wrong call silently loses cells |
| `run_clustering` | resolution against cluster sizes, with no ground truth |
| `find_markers` | whether a cluster's ranked genes cohere as one population |
| `cellranger_count` | the metrics summary is 20 numbers whose interactions carry the meaning |

A small fast model is likely enough for most of group A; these seven are where
a stronger model should earn its cost. Which way each one goes is a
measurement, not an assumption — the point of B is that the choice is per step.

### C — prompt and a tool (2)

| step | what the payload cannot hold |
|---|---|
| `annotate_cells` | whether the assigned cell type's markers actually appear in that cluster's ranked genes — the judge would have to query the marker table |
| `cross_check_annotation` | whether two cell type names are synonyms; Cell Ontology answers this by lookup, and a lookup is verifiable in a way the model's opinion is not |

`cross_check_annotation` is the clearest case for C, and it is worth noting
that C would make it *more* deterministic, not less. Today the model decides
that `CD16+ NK cells` and `CD56-dim natural killer cell` are one population.
Cell Ontology records that relation. Looking it up leaves the model only the
cases that are genuinely ambiguous — which is the right division of work, and
the same argument that put the numeric flags in the step and the vocabulary
question in the prompt.

C also reopens a question this project has answered carefully: a judge that can
call tools is no longer only scoring. The line to hold is that its tools are
read-only, and that whatever it returns is still just a verdict.

## Order of work

1. A schema and an anti-drift test — 25 prompts describing 25 outputs is 25
   sources of truth that can drift from the implementation. A prompt citing
   `per_cluster.confidence` when the field is `median_conf_score` fails
   silently: the judge simply never finds it.
2. Three A prompts: `run_clustering`, `run_qc_metrics`, `detect_doublets`.
3. Measure before and after on identical payloads, model held fixed.
4. Only then decide whether the remaining steps get A, B or C.

Three rather than twenty-five, for the reason the last few commits keep
running into: writing all of them before checking the shape means rewriting all
of them when the shape is wrong.

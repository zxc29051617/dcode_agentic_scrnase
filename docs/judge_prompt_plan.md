# Per-step judge prompts

Where each of the 25 judged steps sits on the A / B / C ladder, what a step
prompt has to contain, and the evidence for doing this at all.

## The evidence

Eight step prompts exist. The first was measured against the real endpoint
before any of the others were written, and it is the reason the rest have the
shape they do. The question it settled was whether the judge would notice that
CellTypist and the marker database name different cell types for the same
cluster — a fact sitting in plain sight in the payload.

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

### B — measured, and not adopted

The mechanism exists (`prompts/step_models.json`, read by
`LocalLLMJudge.model_for`) and ships with no overrides, because the measurement
did not support any.

Measured twice. The first round used six cases and turned on a single one, so
it was redone with fourteen — eight carrying a defect that must not pass, six
clean ones that must. Six clean cases rather than two, because a model that
warns about everything catches every defect and is still useless.

The budget went to cases rather than repeats deliberately: repeats inside one
session are correlated (see the stability finding below), so a second run of
the same case buys less than a case nobody has tried.

| model | 14 cases | missed defects | false alarms | median | slowest |
|---|---|---|---|---|---|
| **`gpt-oss:120b`** | **14/14** | 0 | 0 | 89s | 168s |
| `gpt-oss:20b` | **14/14** | 0 | 0 | 103s | 161s |
| `gemma4:31b` | 13/14 | 0 | 1 | 889s | 1994s |
| `llama3.1:70b` | 11/14 | 0 | 3 | 59s | 109s |
| `gemma4:26b` | 11/14 | 0 | 3 | 256s | **56566s** |
| `ministral-3:8b` | 11/14 | 0 | 3 | **22s** | 28s |
| `gemma4:e4b` | 10/14 | 1 | 3 | 83s | 105s |

**Every failure but one was a false alarm.** No model let a planted defect
through; `gemma4:e4b`'s single miss was an unparseable reply, not a wrong
judgement. What separates the models is entirely whether they can leave a clean
payload alone, and three of the seven could not. That is the worse failure of
the two: a gate that fires on everything teaches a person to click past it.

The same three clean cases tripped all four weak models:

    detect_doublets   rates match the loading prediction
    run_clustering    the corrected basis was used
    run_clustering    X_pca with no integration to correct

The last is the sharpest. `X_pca` is *correct* when nothing was corrected, and
the payload says `integration_ran: false` outright. Four models saw `X_pca` and
warned anyway — they recognise the token and not the context.

**Size does not decide this.** `gpt-oss` scores 14/14 at both 20b and 120b;
every gemma4 raises false alarms at 26b, 31b and e4b alike. `llama3.1:70b` at
42 GB placed fourth. It is a property of the family, not the parameter count.

Two earlier conclusions did not survive the larger case set. `gpt-oss:120b`
scored 14/14 here against 10/12 before — its one earlier failure was the
unstable case described below — so **"the 20b beats the 120b" was an artefact**
of six cases and two correlated runs. The default stays where it was.

From the first round, still standing:

**The small model was not faster.** `gpt-oss:20b` at 13.8 GB took longer than
`gpt-oss:120b` at 65.4 GB — the large one stays resident on the GPU and the
small one is paged in per call. The cost argument for B does not survive
contact with this endpoint.

**The domain model was worse.** `medgemma:27b` missed a doublet rate six times
its expectation, which is the failure that matters, and warned about a clean
clustering, which is the failure that erodes trust in the gates. Medical
fine-tuning did not transfer to reading a metrics payload.

**The fast model is unusable.** `llama3.1:8b` is ten times quicker and wrong
half the time, including passing the doublet storm. A judge that is fast and
wrong is worse than no judge, because the gate it should have opened stays shut.

So `gpt-oss:120b` remains the single model, and the 7 steps below stay at A.
The mechanism is kept because it is 30 lines and the next measurement — a
different endpoint, a model kept resident — can act on its result without
rebuilding anything.

### The stability finding, which outlived the model question

The one case `gpt-oss:120b` failed had passed twice in an earlier session, and
passed three more times when re-run afterwards, on a byte-identical payload at
temperature 0:

    session A    pass, pass
    session B    warn, warn
    session C    pass, pass, pass

Runs inside one session agree with each other and disagree across sessions,
which is what batched inference does. **Repeats within a session therefore
overstate stability** — every "2 of 2 consistent" in this project was measured
that way.

The comparisons survive: before/after arms and the four models were interleaved
inside one session, so whatever the endpoint was doing applied to all arms
alike. What does not survive is reading any single absolute verdict as fixed.
`detect_doublets` on a clean payload sits near this model's pass/warn boundary,
and near a boundary is where a gate opens or does not.

### Steps that stay at A after the B measurement (7)

| step | why the reasoning is different |
|---|---|
| `run_qc_metrics` | trade-offs across several distributions at once, per library |
| `apply_cell_qc_filter` | what a threshold costs is a judgement, not a lookup |
| `detect_doublets` | the observed rate has to be read against the loading it implies |
| `cell_calling_review` | reading a knee from a curve, where the wrong call silently loses cells |
| `run_clustering` | resolution against cluster sizes, with no ground truth |
| `find_markers` | whether a cluster's ranked genes cohere as one population |
| `cellranger_count` | the metrics summary is 20 numbers whose interactions carry the meaning |

These are the seven where the reasoning is hardest, which is why they were the
candidates for a different model. They keep the shared one, and **all seven now
have their own prompt**.

The four written last — `apply_cell_qc_filter`, `cell_calling_review`,
`find_markers`, `cellranger_count` — are **written but not measured**. They
follow the shape the first three established and pass the anti-drift test, which
says every field they cite exists; it does not say the instruction changes what
the judge does. Until `scripts/measure_step_prompt.py` has been run against them
on a real payload, that remains the untested half, and this document should not
be read as claiming otherwise.

Each was written around the reading its own metrics cannot perform:

| step | what its prompt asks that the base prompt does not |
|---|---|
| `apply_cell_qc_filter` | that the preview rows are each criterion *alone*, so they overlap and must never be added — 26 + 72 was 74 removals on the real object, because 24 cells failed both |
| `cell_calling_review` | that the knee and the inflection disagree on purpose, and the gap between them is the range being chosen within, not two competing answers |
| `find_markers` | that significance tracks cluster size, so the expression fractions carry the biology and the p-values only say the difference was not noise |
| `cellranger_count` | that the meaning is in how the metrics sit against each other, and that every value arrives as the string Cell Ranger wrote to CSV — `"1,219"`, `"95.5%"` — never as a number |

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

**Not started.** It is a larger piece than A and B together, and starting it
half-way would leave the worst of both: a judge with tool-calling wiring and
nothing verifiable behind it. What it needs first, in order:

1. **A read-only tool contract.** Today `judge()` sends one message and parses
   one reply. Tool calling makes it a loop, and every loop needs a bound: how
   many calls, what happens when the tool errors, and what the verdict says
   when the model asked for something that does not exist. None of that exists.
2. **The ontology itself.** Cell Ontology is an OWL file; the useful subset is
   the synonym and `is_a` relations between the labels CellTypist and
   scMayoMap actually emit. That subset should be extracted, committed as
   plain text next to `marker_db/`, and given the same provenance record — the
   pattern is already there.
3. **A measurement with a decidable answer**, as in A and B. For synonyms that
   is easy to build: the fifteen pairs from the PBMC run have known answers,
   and a lookup either reproduces them or does not.
4. **Only then the model.** If the lookup resolves twelve of fifteen pairs
   deterministically, the model is left with three, and the argument for
   letting it call the tool at all gets much weaker than the argument for the
   step calling the tool and putting the answer in the payload.

Point 4 is the one to settle first, because it may remove the need for C
entirely: a deterministic lookup performed by the *step* needs no tool-calling
judge, keeps the model out of the loop, and is verifiable. That is the same
move that put the numeric flags in `cross_check_annotation` and left only the
vocabulary question to the prompt.

## Order of work

1. ~~A schema and an anti-drift test~~ — done. `prompts/steps/README.md` and
   `tests/test_step_prompts.py`.
2. ~~Three A prompts~~ — done: `run_clustering`, `run_qc_metrics`,
   `detect_doublets`.
3. ~~Measure before and after on identical payloads, model held fixed~~ — done,
   plus negative controls, which is where `run_clustering`'s missing embedding
   provenance turned up.
4. ~~Decide whether the remaining steps get A, B or C~~ — B measured and not
   adopted; C scoped above and not started.
5. ~~Four more A prompts, for the group-B steps that have none~~ — written:
   `apply_cell_qc_filter`, `cell_calling_review`, `find_markers`,
   `cellranger_count`. The shape was settled, and it held: none of the four
   needed a structural change, and the drift test caught one thing —
   `needs_human_review` is a `JudgeResult` field rather than a step's, so it
   joined `suggested_action` in the test's `NOT_A_FIELD` set.
6. **Next**: measure those four.
   `python scripts/measure_step_prompt.py <step> --run <run_dir>` over a saved
   payload, with and without the file, the model held fixed and the arms
   interleaved inside one session — the stability finding above is why the
   interleaving matters and why repeats inside a session prove less than they
   appear to. Four prompts that pass the drift test are four prompts whose
   effect nobody has measured.

   **The four need three different runs between them**, which is the part to
   plan for rather than discover. The harness reads
   `<run_dir>/<step>/output.json`, so a step that never ran has no payload:

   | step | the run it needs |
   |---|---|
   | `cellranger_count` | any FASTQ-route run |
   | `apply_cell_qc_filter` | any run that reached the QC gate — a filtered-matrix run stopping there is enough |
   | `cell_calling_review` | a **raw**-route run; the filtered route never visits this step |
   | `find_markers` | a run carried past clustering, which means answering the QC and cell-count gates first |

   The first two come free with any FASTQ run that stops at the QC gate. The
   last two do not, and `find_markers` in particular cannot be measured without
   driving a run to completion.
7. Then the twelve remaining A steps, or stop: a prompt is only worth writing
   where the base prompt would miss something, and for a structural check with
   six numbers in its payload it probably would not.

Seven rather than twenty-five, for the reason the last few commits keep
running into: writing all of them before checking the shape means rewriting all
of them when the shape is wrong. The shape held — the first three needed no
structural revision after measurement, only the one field-name fix the drift
test caught, and the four that followed needed none either. What has not been
established is that the four *work*, which is step 6 and not a formality: the
one arm that mattered in the original measurement was the one that added data
and changed nothing.

# Working context

A single-cell RNA-seq pipeline built as a **hybrid multi-agent workflow**:
LangGraph fixes the order of the analysis, and specialist judges are integrated
at particular nodes rather than everywhere. Deterministic Python does the
analysis; a model scores each step and can stop the run at a human gate; it
never writes an analysis result.

## Where this actually stands

The LangGraph mainline is complete and runs end to end on real data.

**Judge specialisation is partial and being extended.** Of 26 registry steps,
25 are judged, and **8 have their own step prompt** — `run_qc_metrics`,
`detect_doublets`, `run_clustering`, `cross_check_annotation`, and the four
added since: `apply_cell_qc_filter`, `cell_calling_review`, `find_markers`,
`cellranger_count`. The other 17 use the shared base prompt.

**All eight have been measured against a real endpoint**, before and after,
three runs per arm. The results are not uniform and the differences are the
point: `find_markers` changed verdict, `apply_cell_qc_filter` gained a stable
score where the base prompt wandered, `cell_calling_review` gained the reading
a person decides from, and `cellranger_count` showed no effect on the only
payload available — which is a gap in the evidence, not a verdict on the file.
The numbers, the run ids and what is still owed are in
`docs/judge_prompt_plan.md`.

Do not describe this layer as finished.

## Read before changing anything

| | |
|---|---|
| `docs/judge_prompt_plan.md` | how far judge specialisation goes, what was measured, what is deliberately not built |
| `docs/judge_setup.md` | judge backends, endpoints, what leaves the machine |
| `docs/report_contract.md` | what `build_report` may and may not do |
| `prompts/steps/README.md` | the shape a step prompt has to take, and why |
| `git log` | the reasoning behind each change; commit messages here are long on purpose |

## How to work here

**Measure before claiming.** Every performance or quality claim in this repo
has a measurement behind it, several of which overturned the expectation that
prompted them. A change described as an improvement without evidence is not
finished. When comparing two options, keep a control arm — the one that rules
out the explanation you did not test.

**Small changes, existing shapes.** Match the surrounding architecture, naming
and comment style. Copy the closest existing skill rather than inventing a new
structure.

**Do not delete, overwrite or revert existing work** unless asked to. That
includes rewriting a file wholesale when an edit would do.

**Keep the repo clean.** One-off debugging scripts and run outputs do not
belong in it. `runs/` costs roughly 400 MB per run and is gitignored; keep what
matters in `results/` and delete the run. `bash scripts/run_disk_usage.sh`
reports what has accumulated.

**Say when something is wrong.** A failing test, an unexpected result, or a
point of genuine uncertainty gets stated plainly. Do not route around it
quietly, and do not soften a negative result into a positive one.

**Run the tests.** `python tests/run_all.py` for everything, or the module that
covers what you touched. Some tests skip without the real matrices linked into
`data/`; skips are fine, failures are not.

## Environment

```bash
conda activate dcode-scrna
python -m src.run --input <matrix-or-fastq> --species human    # --judge stub by default
```

The judge needs an endpoint; see `docs/judge_setup.md`. Tests need none.

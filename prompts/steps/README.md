# prompts/steps/

One optional file per step, `<step>.md`, appended to `local_judge_base.md` when
that step is judged. Steps without a file get the base prompt untouched.

## Why these exist

The base prompt asks whether a step ran soundly. For most steps that is the
whole question. It is not enough when the reading a step needs is one its own
metrics cannot perform — and measuring that produced the only reason to write
any of this down:

| arm | runs that caught the disagreement |
|---|---|
| payload as built | 0 / 3 |
| payload with the facts promoted to their own field, and a warning naming the question | 0 / 3 |
| payload as built, plus a step prompt | 3 / 3 |

Adding data did nothing. The instruction did the work.

## Required shape

`tests/test_step_prompts.py` enforces all of this.

```markdown
## For `<step_name>` specifically

<one or two sentences: what this step did, in terms the judging needs>

### What to judge
### What the numbers cannot show
### Worked examples
### When to warn
```

The four sections are required because each one earned its place in the prompt
that worked:

**What to judge** — a task, not a topic. "Decide whether the resolution
produced clusters a person would defend", not "consider the clustering". The
base prompt already asks the generic question; this file exists to ask the one
it does not.

**What the numbers cannot show** — the metrics' blind spot, stated outright.
This is the section most likely to be skipped and most likely to matter: for
`cross_check_annotation` the numeric flags never read a cell type's name, so the
clearest disagreement in the whole run arrives carrying no flag at all. A judge
told only to weigh the flags will report that nothing is wrong, accurately.

**Worked examples** — two or three from this domain, with the answer. Without
them the judge applies a generic sense of "unusual" and flags whatever is
merely uncommon.

**When to warn** — otherwise the score is arbitrary, and two runs over one
input disagree.

## Every field you name must exist

A prompt that cites `per_cluster.confidence` when the field is
`median_conf_score` fails silently — the judge looks, finds nothing, and reports
on what it did find. Nothing errors, and the prompt looks fine.

So the test resolves every backticked snake_case identifier against the step's
own implementation, and fails on any it cannot find. Rename a field without
updating its prompt and the suite says so.

This is the same failure the project keeps meeting: a second description of the
code, drifting away from it. These files cannot be generated from the code —
the judgement in them is the point — so they are checked against it instead.

## Adding one

1. Copy `cross_check_annotation.md` — it is the one with measured evidence
   behind its shape.
2. Rewrite the four sections for the step. Name real fields; the test checks.
3. Measure it: `python scripts/measure_step_prompt.py <step> --run <run_dir>`
   runs the judge with and without the file over one saved payload and prints
   both verdicts. A prompt nobody measured is a prompt nobody knows the effect of.

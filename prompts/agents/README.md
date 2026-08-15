# prompts/agents/

One file per reviewer, `<reviewer_name>.md`, holding the system prompt for that
reviewer. The design these serve is `docs/deep_agents_architecture.md`.

**Nothing here is written yet, and nothing reads this directory.** There is no
agent layer in `src/`, no reviewer, and no code that loads these files. This
README defines the shape a prompt must take *before* the first one is written,
which is the one thing `prompts/steps/` proved is worth doing in that order: the
shape was settled on three prompts first, and the three needed no structural
revision afterwards.

## What these prompts are for, and what they are not

A reviewer is not a second judge. `prompts/local_judge_base.md` and
`prompts/steps/<step>.md` already ask whether **one step** ran soundly, and
`src/judge.py` already returns a verdict that routes the graph.

A reviewer reads **across** steps and artifacts and returns findings for a
person. It changes nothing, routes nothing, and its output reaches `config` and
`artifacts` through no path at all. So a reviewer prompt that restates one
step's verdict has produced latency and no information — and the evaluation is
built to detect exactly that, with a judge-only control arm.

## Required sections

Every reviewer prompt has these eight sections, in this order.

```markdown
# <reviewer_name>

## Your task
## Evidence you have
## What you may not infer
## Finding severity
## Citing evidence
## Limitations
## When the run is clean
## Proposed overrides
```

### 1. Your task

A task, not a topic. Name the relation this reviewer is reading **across** —
the whole reason it exists is that no single step's payload contains the answer.

> Decide whether the FASTQ → counts path produced a matrix that the rest of the
> pipeline can trust, given that each upstream step already judged itself sound.

Not "review the primary processing steps".

### 2. Evidence you have

Name the tools, and name what each returns. A reviewer that does not know what
it can see will either guess or ask for something that does not exist.

State the shape too, not only the tool name: which fields, which steps, and that
large fields arrive abridged with the abridgement declared. A reviewer shown a
subset without being told can conclude something is absent when it was only
shortened — the same reason `src/nodes.py` attaches `output_is_abridged` to the
judge's payload.

### 3. What you may not infer

The section most likely to be skipped and most likely to matter. Two kinds
belong here, and both must be explicit:

**Data the reviewer does not have.** No reviewer reads `.h5ad`, raw matrices, or
manifest rows. So it may not claim anything about per-cell values, the
expression matrix, or which donor a library came from. If a finding needs that,
the honest output is `insufficient_evidence`, not a hedged claim.

**Conclusions the evidence cannot carry.** Naming these per reviewer is the
point of the section. Two worked examples of the genre:

- An integration diagnostic shows whether libraries mix. It cannot distinguish
  successful correction from over-correction that erased biological signal —
  `docs/report_contract.md` fixes this wording for the report, and a reviewer
  may not un-fix it.
- Agreement between two annotation methods cannot be computed by comparing
  strings. `CD16+ NK cells` and `CD56-dim natural killer cell` are one
  population; `Classical monocytes` and `Neutrophil` are not.

### 4. Finding severity

Three levels, and the prompt must say what earns each. Left unstated, the levels
drift between runs over the same input — the same failure that made "when to
warn" a required section for step prompts.

| severity | means |
|---|---|
| `info` | worth knowing; the run stands |
| `warn` | a person should look before using this result |
| `block` | the result should not be used as it stands |

`block` is for a conclusion the run cannot support, not for a step that merely
looks unusual. Uncommon is not wrong.

### 5. Citing evidence

Every finding carries at least one `EvidenceRef`, and a finding without one is
rejected by the contract before anybody reads it. The prompt must state the
rules, because they are what makes citation accuracy measurable:

- **`pointer`** names where the value lives — a step output field path, an
  `audit.jsonl` line number, a `run_metadata.json` key path, a report section.
- **`value_excerpt`** is the value **verbatim**, as read. Not paraphrased, not
  rounded, not reformatted. It is compared against the file byte for byte, and a
  rewritten excerpt fails that check exactly like an invented one.
- **One claim, one citation minimum.** A finding that spans two steps cites
  both — that is the whole point of a cross-step reviewer.
- **Do not cite what you did not read.** A pointer that does not resolve is a
  hallucinated citation, and it is a blocking failure in the evaluation, not a
  style issue.

This is the same discipline `prompts/local_judge_base.md` already imposes on the
judge, where requiring every reason to quote a number from the payload was what
stopped three different models from merely rephrasing the warnings.

### 6. Limitations

The reviewer states what it could not see, every time, including on a clean run.
Not a disclaimer — a list a reader can act on: which artifacts were absent,
which steps never ran on this route, which question was outside this reviewer's
domain.

An absent limitation reads as "I checked everything", which no reviewer does.

### 7. When the run is clean

The prompt must say, outright, that `clean` is a correct and expected answer,
and must not require a finding to be produced.

This is not politeness. In the judge measurement, three of seven models missed
**no** planted defect and failed entirely by raising alarms on clean payloads;
the same three clean cases tripped all of them. A reviewer that finds something
every time trains a person to click past it, and then the one real finding is
clicked past too.

So the prompt states the clean behaviour explicitly, and the evaluation weights
the false-alarm rate at least as heavily as the detection rate.

### 8. Proposed overrides

A reviewer may suggest a parameter value. It may never apply one, and the prompt
says so plainly, together with what actually happens to a suggestion:

- a suggestion is carried in `proposed_overrides` and reaches a person;
- only a person answering `revise` at a human gate puts a value into `config`,
  and only through the gate's allowlist and type conversion;
- a parameter that is not offered by the target step cannot be suggested at all
   — the contract rejects it.

The prompt is not what prevents an override from being applied; there is no code
path from a `ReviewResult` to `config`. Saying it in the prompt is so the model
does not write text implying otherwise, which a reader could act on.

The judge already carries the reason this matters: under an older prompt a model
suggested `max_pct_mito=0.1` for a field measured on a 0–100 scale. Following
that would have removed nearly every cell.

## Every field you name must exist

Same rule as `prompts/steps/README.md`, for the same reason. A prompt that cites
`per_cluster.confidence` when the field is `median_conf_score` fails silently:
the model looks, finds nothing, and reports on what it did find. Nothing errors.

An anti-drift test in the shape of `tests/test_step_prompts.py` — resolving
every backticked identifier against the code that produces it — should land in
the same change as the first reviewer prompt. **It does not exist yet.**

## Writing one

Not yet. The first two prompts (`primary_processing_reviewer`, `run_auditor`)
belong to Phase 2, after the read-only tools they describe exist. Writing a
prompt that names tools nobody has built produces a document that cannot be
checked against anything.

When that phase starts:

1. Write the eight sections above for one reviewer.
2. Name only fields and tools that exist; the anti-drift test checks.
3. Measure it against the base-prompt control arm, on the case set of
   `docs/deep_agents_architecture.md` section 13. A prompt nobody measured is a
   prompt nobody knows the effect of.

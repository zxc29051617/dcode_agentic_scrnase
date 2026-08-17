# Deep Agents architecture

Phase 0. This document is a design and a set of decisions. **None of it is
implemented**, and this commit adds no Python, no dependency and no change to
the graph.

The question it answers is narrow: the pipeline already scores every step with a
judge and stops at a human gate, so what is left for an agent layer to do, and
where must that layer be forbidden from reaching?

## Status

| | |
|---|---|
| Implemented | nothing in this document |
| Added by this commit | this file, and `prompts/agents/README.md` |
| Dependencies changed | none. `deepagents` is **not** installed |
| Executor changed | none. `src/`, `skills/`, `graph.mmd` untouched |

Section 15 lists what is not built, in one place, so this file cannot be read as
a description of working code.

`docs/copilotkit_product_architecture.md` is the companion Phase 0 document for
the surrounding product boundary: Current CLI behaviour, the Near-term
read-only FastAPI / AG-UI / CopilotKit surface, and the Production target for
workers, external web gates and this review layer. This document remains the
narrow Deep Agents design. Where the documents overlap, the scientific worker is
the only writer of artifacts, checkpoints, audit, metadata and reports; Deep
Agents remain completely read-only and never answer `accept`, `revise` or
`stop`.

---

## 1. What exists today

### 1.1 The executor

`src/graph.py::build_graph()` compiles the workflow: **56 nodes, 81 edges**
(`docs/graph.mmd`). It owns, and will keep owning:

| | where |
|---|---|
| which steps exist, their kind, their judge, their revisable parameters | `src/registry.py` — 26 steps |
| topology: who follows whom, conditional routing, gate wiring | `src/graph.py` |
| step execution — `skills/<name>/<name>.py::run(payload)`, imported directly | `registry.call_skill` |
| workflow state between nodes | `src/state.py::WorkflowState` |
| pausing at a gate, and picking it up in another process | `src/persistence.py` |
| deciding what a resumed run may reuse | `persistence.plan_resume` |
| the append-only record of what happened | `src/provenance.py` |
| the report | `skills/build_report`, `docs/report_contract.md` |

Two routes (FASTQ, count matrix), a raw/filtered split, and a cell-calling
branch. Every branch predicate is Python over state, which is the first reason
an agent does not get to route: a conditional edge is a function, and a table
cannot hold it.

### 1.2 The shared judge

One implementation, one contract: `src/judge.py::JudgeResult`, mirroring
`schemas/judge_result.schema.json`. There is no `skills/judge_*` — that design
was considered and dropped (`skills/README.md`), and `REGISTRY[step].judge` is
the **name of a graph node and a label in the audit log**, not a module path.

- 25 of the 26 steps are judged. `human_review_decision` is a gate and has no
  judge.
- 4 steps have their own prompt: `run_qc_metrics`, `detect_doublets`,
  `run_clustering`, `cross_check_annotation`. The other 21 use the shared base
  prompt. `docs/judge_prompt_plan.md` holds the plan and the measurements.
- The judge reads **one step's payload** and returns a verdict plus `advice`.
- It cannot write an analysis result. Not because it is asked not to: the judge
  node returns `{"judge_results": [...]}` and nothing else
  (`src/nodes.py:224`), so no key exists through which a suggested value could
  reach `artifacts` or `config`.

What the judge structurally cannot see: the previous step, the report, the audit
log, the study design as a whole. It is given one step's numbers and asked
whether that step ran soundly.

### 1.3 The human gate

Two gates, four nodes.

| gate | nodes | asks about |
|---|---|---|
| escalation (H1) | `human_gate` → `human_gate_answer` | one step whose verdict was `warn`/`fail`, or that asked for review |
| mainline (H2) | `human_review_decision` → `human_review_decision_answer` | the run as a whole; `revise` re-enters at `annotate_cells` |

Asking and answering are separate nodes because `interrupt()` raises out of its
own node, so a single node can never both ask and record the asking.

`revise` may carry `overrides`, and they are the only way a value reaches
`config` after a run has started. They are checked against the gate's own
allowlist and converted by `REVISABLE_PARAMETERS`
(`src/registry.py:90`, `src/registry.py:226`); refusals come back as sentences
for the person who typed them, never as a silent drop.

Two steps refuse to continue even on `accept`, because accepting cannot
manufacture the artifact: `apply_cell_qc_filter` without thresholds, and
`cell_calling_review` without a cell count.

### 1.4 Where state actually lives

This is the part an agent layer is most likely to get wrong, so it is stated
precisely.

**`runs/<run_id>/checkpoint.sqlite` holds LangGraph graph state that
`--continue-from` can resume from.** A run started with `--interactive` writes
it; a gate that suspends there survives the process, and `continue_workflow`
picks up the pending question and answers it in a new interpreter. The state
behind the interrupt — every reduced field of `WorkflowState` — is in that file.

**The checkpoint can only answer where the graph stopped.** It cannot answer
whether the artifacts on disk are still valid, and it must never be asked to.
Delete an `adata.h5ad`, or re-run one step through its standalone CLI — which
this project supports for all 26 — and the checkpoint still says the step is
complete.

**Artifact validity is decided from the run directory, not from the
checkpoint.** `persistence.plan_resume` (`src/persistence.py:379`) reads:

| source | question it answers |
|---|---|
| `runs/<id>/<step>/output.json` | what did this step return |
| `runs/<id>/audit.jsonl` | what was this step's final recorded outcome |
| `runs/<id>/run_metadata.json` | what config and input produced this directory |
| `config_sha256` / `input_digest` diff | which is the earliest step that can no longer be trusted |
| `artifacts_present()` on the recorded paths | does every file this step claimed to write still exist |

It fails closed at every point where it cannot tell.

**The two resume semantics must not be merged.** They answer different
questions:

| flag | question | reads |
|---|---|---|
| `--resume-from RUN_ID` | which results are still valid | the run directory (the five sources above) |
| `--continue-from RUN_ID` | where did this run stop | `checkpoint.sqlite` |

They can disagree, and the disagreement is informative — a checkpoint says a
step completed, the artifact check says its output is gone. Merging them would
force a silent choice of winner and produce a report describing a run that did
not happen. The agent layer inherits this rule unchanged and does not get to
soften it.

### 1.5 What does not exist

There is no coordinator, no subagent, no reviewer and no agent layer of any
kind. A tree-wide search for `deepagents`, `subagent`, `coordinator` and
`reviewer` finds only `docs/report_contract.md`, where "reviewer" names a
**reader** of the report. `deepagents` is not in `environment.yml`, not in
`conda-lock.yml`, and not installed in the `dcode-scrna` environment.

---

## 2. Why an execution layer and a review layer, rather than more of either

**Why not just more judges.** The judge is per step and single shot. Several of
the failures this pipeline exists to catch are not visible from one step's
payload:

- `resolve_reference` claims human; `cellranger_count` reports a mapping rate
  that no human reference would produce. Each step's own numbers look fine.
- The manifest is well-formed and `technical_batch` was filled in with
  `donor_id`. `run_integration` sees several batches, no confounding, and
  removes every individual difference. `docs/study_design.md` records exactly
  this failure, and the reason there is deliberately no `auto` mode.
- `run_metadata.json` lists two judge sessions and every verdict cites the
  first. Nothing is wrong with any single step.

None of these is a step doing its job badly. Each is a *relation between
steps*, and the judge has no view of relations.

**Why not 26 agents.** Turning each step into an agent would replace 26
deterministic, tested, individually runnable skills with 26 things that give a
different answer on Tuesday. The skills are the part of this project that
already works; the argument for an agent is about the reading, not the running.

**So the split is:**

| layer | scope | determinism |
|---|---|---|
| LangGraph executor | run the analysis, route, gate, record | deterministic; a seed and a config reproduce it |
| shared judge | score one step | model, bounded to one payload, cannot write |
| Deep Agents reviewers | read across steps and artifacts, report findings | model, read-only, cannot write anything at all |
| human | decide | — |

The layers are ordered by how much damage a wrong answer does, and the amount
of freedom each gets is inverse to that.

---

## 3. Architecture decisions

Recorded so a later reader can tell a decision from an accident. Each is
falsifiable: if the stated reason stops being true, the decision is open again.

**AD-1. The existing LangGraph is the only workflow executor.**
No second execution path is built. The agent layer never calls `call_skill`,
never builds a graph, and never writes to a run directory that a workflow owns.
*Falsified if* an agent needs to produce an artifact — at which point the answer
is a new deterministic step, not an agent.

**AD-2. Deep Agents is a read-only research review layer.**
It reads what a run wrote and returns findings. It produces no analysis result,
and nothing it returns becomes a setting on its own.
*Falsified if* a finding cannot be expressed without recomputing something — at
which point the computation belongs in the step that owns it, which is the same
rule `docs/report_contract.md` already applies to `build_report`.

**AD-3. Long term: 1 Coordinator + 6 domain reviewers.**
Not 26. The six domains are review responsibilities, not a regrouping of skills
(section 5.3). No skill moves, no `StepSpec` changes, no edge changes.

**AD-4. MVP: 1 Coordinator + 2 reviewers** — `primary_processing_reviewer` and
`run_auditor`. Chosen because the next milestone is a public FASTQ golden run,
which is exactly the first reviewer's domain, and because the auditor's findings
are machine-checkable, which makes it the anchor for the evaluation.

**AD-5. The shared judge keeps step-level scoring.**
It is the first check, it stays per step, and its verdict keeps driving
`policy.route()`. The reviewers do not replace it and do not read its prompts.

**AD-6. Reviewers do cross-step and cross-artifact review only.**
A reviewer that only restates one step's verdict is adding latency, not
information. The evaluation (section 13) has a judge-only control arm precisely
to detect this.

**AD-7. The human gate stays the decision point.**
`accept` / `revise` / `stop` is answered by a person. Overrides continue to
reach `config` only through `coerce_overrides`. No agent output is wired into
the gate.

**AD-8. Permission is enforced by tools and code, never by prompt.**
An instruction not to do something is not a control. The reviewers get a small
list of read-only functions and no filesystem, so the forbidden operations have
no implementation to reach (section 6.3).

---

## 4. Data flow

```
User question
  │
  ▼
Main Coordinator            reads the question, builds context, delegates
  │
  ├──► primary_processing_reviewer ──┐   read-only, isolated context
  └──► run_auditor ──────────────────┤   each returns one ReviewResult
                                     │
  ◄──────────────────────────────────┘
  │
  ▼
Main Coordinator            integrates; contradictions are reported, not resolved
  │
  ▼
Human                       reads; decides; acts
  │
  ▼
Existing LangGraph executor   python -m src.run …   (semantics unchanged)
  │
  ▼
runs/<id>/  artifacts · output.json · audit.jsonl · run_metadata.json · report
  │
  └──► input to the next review
```

The arrows into the executor are drawn through a person on purpose. In the MVP
they are not drawn by code at all: no tool exists that can start, resume or
answer anything.

**The executor never reads anything the agent layer wrote.** That is what keeps
the agent layer from becoming a second source of truth about the run.

---

## 5. Responsibilities

### 5.1 Main Coordinator

| may | may not |
|---|---|
| read the user's research question and turn it into review tasks | execute analysis, or call any skill |
| call the read-only `inspect_*` tools to build context | read `.h5ad`, raw matrices, or manifest rows |
| delegate to reviewer subagents, in parallel | modify config, artifacts, `run_metadata.json` or `audit.jsonl` |
| integrate several `ReviewResult`s and name where they disagree | answer a human gate, or decide `accept`/`revise`/`stop` |
| produce a summary for a person, and a suggested next command as text | run that command |
| say "insufficient evidence" and stop | state a conclusion the evidence does not carry |
| pass `proposed_overrides` through verbatim | apply a `proposed_override` anywhere |

Where two reviewers disagree, the coordinator reports both with their evidence.
Adjudicating between them is a judgement about the science, and the person
reading has the context the coordinator does not.

### 5.2 Reviewer subagents

Every reviewer, in every phase:

- reads only through the `inspect_*` tools;
- returns exactly one `ReviewResult` (section 7);
- attaches at least one `EvidenceRef` to every finding, or the finding is
  rejected by the contract before anyone sees it;
- reports `clean` when the run is clean. A reviewer that finds something every
  time is a reviewer nobody reads — see section 13 on why the false-alarm rate
  is weighted at least as heavily as the detection rate.

### 5.3 The six domains, and the 26 steps

Each step has exactly **one primary owner**, so two reviewers can never issue
contradictory `recommended_action`s about the same step. Others may cite a step
as evidence ("consulted") but may not own a recommendation about it.

| # | Reviewer | what it reads across | primary steps | n |
|---|---|---|---|---|
| 1 | Study Design | library ↔ specimen ↔ subject ↔ group ↔ technical batch | `ingest_validate`, `sample_qc_triage`, `merge_samples` | 3 |
| 2 | **Primary Processing** | reads → counts → a trustworthy AnnData handover | `resolve_reference`, `matrix_preflight`, `fastq_preflight`, `fastq_qc`, `cellranger_count`, `count_matrix_classify`, `post_load_validate` | 7 |
| 3 | Cell Quality | which barcodes are cells, which cells are kept | `load_raw_counts`, `load_filtered_counts`, `cell_calling_review`, `run_qc_metrics`, `apply_cell_qc_filter`, `detect_doublets` | 6 |
| 4 | Representation / Integration | which space the cells are viewed in, and what was removed | `normalize_hvg_prepare`, `run_pca`, `run_integration`, `run_clustering`, `run_umap` | 5 |
| 5 | Annotation | what the clusters are, and whether two methods agree | `find_markers`, `annotate_cells`, `cross_check_annotation` | 3 |
| 6 | **Report / Provenance Auditor** | whether the record matches what happened | `human_review_decision`, `build_report` | 2 |
| | | | **total** | **26** |

Consulted, not owned:

- Study Design on `run_integration` (confounding is a design question),
  `apply_cell_qc_filter` and `detect_doublets` (both per library).
- Primary Processing on `cell_calling_review` — `force_cells` bypasses
  EmptyDrops, and the divergence from Cell Ranger's own call is an upstream
  fact.
- The Provenance Auditor on all 26, because it audits the record rather than
  the science.

**This is a grouping of review responsibility only.** No skill moves. No
`StepSpec` changes. No graph topology changes.

---

## 6. The MVP: two read-only reviewers

### 6.1 `primary_processing_reviewer`

Question: is this FASTQ → counts path trustworthy — species, chemistry, read
roles, Q30, mapping rate, the raw/filtered call, and does the object handed to
the mainline match what the upstream steps said they produced?

Reads: `inspect_step` over its 7 steps, plus `inspect_run`.

Example finding: `resolve_reference` recorded a human transcriptome;
`cellranger_count`'s metrics summary reports a mapping rate no human reference
would produce. Neither step's own verdict is wrong.

### 6.2 `run_auditor` — scope, decided

**Version 1 audits the record only.** It checks:

| | |
|---|---|
| report availability | which report sections rendered, and whether each unavailable one states a reason |
| P0–P9 provenance | the audit tier of `docs/report_contract.md` is present and readable |
| judge session ↔ verdict | every `judge` audit event's `judge_session_id` resolves to an entry in `run_metadata.json`'s `judge_sessions`, and every recorded session is accounted for |
| revisions | `run_metadata.json`'s `revisions` list is consistent with the `human_gate_close` events |
| `resume_plan` | what was reused, what it re-ran from, and whether the stated reasons are internally consistent |
| `checkpoint_resumed` | whether a continue actually happened, as distinct from a checkpoint merely existing |
| config / input digest consistency | `config_sha256` matches the recorded `config`, and moved with any recorded revision |

**Version 1 does not judge whether the report's scientific narrative is
correct.** That is a different capability with a different evaluation, and
mixing them would make the auditor's error rate uninterpretable — the whole
reason it was chosen for the MVP is that its findings can be checked
mechanically.

### 6.3 What must not exist in version 1

These are absent from the codebase, not merely disallowed by a prompt. In v1
there is **no implementation** of any of:

| | |
|---|---|
| `start_workflow` | — |
| `resume_from_artifacts` | — |
| `continue_human_gate` | see 6.4 |
| `execute` (shell) | — |
| arbitrary `read_file` | — |
| `glob` | — |
| `grep` | — |
| `write_file` | — |
| `edit_file` | — |
| `delete` | — |

The agent layer is given **no filesystem backend pointing at `runs/`**. All data
arrives through a small set of read-only `inspect_*` functions that return
dicts. That is the control; everything else is defence in depth.

Planned read-only tools for v1 (design only — not implemented):

| tool | returns |
|---|---|
| `inspect_run(run_id)` | a `summarize()`-shaped overview: status, steps run, verdicts, halt reason, pending review |
| `inspect_step(run_id, step)` | that step's `output.json`, with large fields abridged and the abridgement declared |
| `inspect_audit(run_id, events=[…])` | filtered `audit.jsonl` lines, with line numbers so they can be cited |
| `inspect_run_metadata(run_id)` | `run_metadata.json`, redacted |
| `inspect_study_design(run_id)` | counts, the contingency table, the column list and `manifest_sha256` — **never rows** |
| `inspect_report(run_id)` | `report.md` section text, with inline figure data URIs stripped |
| `inspect_resume_history(run_id)` | `resume_plan` and `checkpoint_resumed` events, and `revisions` |

Abridging follows the pattern already in `src/nodes.py` for the judge's view: a
named projection of known structure, with a note saying what was shortened,
rather than a byte cap that could cut away the evidence a finding turns on.

### 6.4 `continue_human_gate`

**Not available in the MVP.** No tool, no wrapper, no approval-gated variant.

If it is ever evaluated, the first thing to establish is **provenance of the
human operator's identity**: `human_gate_close` records an `operator`, and that
field is what the report's P3 section reports and what a reader six months later
uses to know who decided. A path where a model composes the answer and a person
approves it must be able to write the real operator's identity into that record,
distinguishably from a decision the person typed themselves. Until that is
designed and tested, the gate is answered at the terminal.

This is a deferral, not a permanent exclusion, and nothing here designs the
implementation.

---

## 7. The review contract

One schema for every reviewer, so results can be compared, cached, diffed and
checked. Pydantic, `extra="forbid"`, matching the vocabulary already used by
`src/judge.py`'s `JudgeResult` and `Advice`.

Fields:

| field | meaning |
|---|---|
| `schema_version` | so a stored result stays interpretable |
| `reviewer` | which reviewer produced it |
| `run_id` | which run it is about |
| `status` | `clean` / `concerns` / `blocked` / `insufficient_evidence` |
| `findings[]` | `finding_id`, `severity` (`info`/`warn`/`block`), `claim`, `steps[]`, `evidence_refs[]` |
| `evidence_refs[]` | `kind`, `run_id`, `step`, `pointer`, `value_excerpt` — **at least one per finding** |
| `recommended_action` | `none` / `inspect` / `rerun_step` / `revise_parameter` / `stop_run` / `ask_human` |
| `proposed_overrides[]` | `parameter`, `suggested_value`, `target_step`, `rationale`, `confidence` |
| `confidence` | `low` / `medium` / `high` |
| `limitations[]` | what this reviewer could not see |
| `evidence_snapshot` | the state fingerprint the review was written against (section 8) |

Three constraints, each enforced by the type rather than by an instruction:

1. **A finding with no `evidence_ref` is rejected.** This makes citation
   accuracy machine-checkable: resolve the `pointer`, compare `value_excerpt`
   against the file. It is the same discipline `prompts/local_judge_base.md`
   already imposes on the judge, moved into the schema.
2. **`proposed_overrides` validate against the existing allowlist.** The
   `parameter` must be in `REVISABLE_PARAMETERS` and offered by `target_step`;
   the same `coerce_overrides` path the gate uses. A reviewer cannot invent a
   parameter name, because the validator refuses it.
3. **`proposed_overrides` carry `applied: Literal[False]`.** No valid
   `ReviewResult` can claim an override took effect.

---

## 8. State and persistence boundaries

| thing | where | owner | source of truth for |
|---|---|---|---|
| Deep Agents conversation / task state | v1: in memory only. Later: a separate product review store, never `runs/<id>/` | agent layer | the review conversation, and nothing else |
| reviewer output | a separate product review store keyed by run id and evidence snapshot, never the scientific run directory | agent layer | what a model said |
| LangGraph graph state | in memory; when paused, `runs/<id>/checkpoint.sqlite` | LangGraph | **where the graph stopped** |
| step output | `runs/<id>/<step>/output.json` and the artifacts it names | skills | **which results are still valid**, together with the audit log and metadata |
| events | `runs/<id>/audit.jsonl`, append-only | `provenance.AuditLog` | **what happened** |
| environment, config, judge sessions, revisions | `runs/<id>/run_metadata.json` | `provenance` | **what produced this run** |

**`checkpoint.sqlite` is owned by LangGraph exclusively.** The agent layer does
not read it. `inspect_resume_history` reads `checkpoint_resumed` **audit
events**, not the database — a checkpoint on disk means a run *could* have been
picked up; only the event means it *was*, and `docs/report_contract.md` already
reports these as two separate facts.

**Avoiding contradiction between agent memory and the run's own record.** Three
rules:

1. Every `inspect_*` call re-reads from disk. No cross-turn caching.
2. Every `ReviewResult` embeds an `evidence_snapshot` — audit line count,
   `config_sha256`, metadata mtime — the fingerprint it was written against.
3. Before a review is shown to anyone, the snapshot is re-checked. A mismatch
   marks the review **stale** and its conclusions are withheld.

So agent memory cannot become a second source of truth: it records "what I saw
at fingerprint X", and X is refutable.

**`--resume-from` and `--continue-from` keep their current semantics exactly.**
The MVP calls neither. `tests/test_durable_resume.py`,
`tests/test_resume_validation.py` and `tests/test_cli_env.py` must keep passing
without a single edit — that is the cheapest check that nothing in the executor
moved.

---

## 9. External human gates, later

The MVP has no control tool, and the product target does not give one to Deep
Agents. A reviewer may explain the pending question or make a cited proposal;
it may never start a run, resume a run, alter a setting, or answer
`accept`/`revise`/`stop`.

**External human-gate contract.** A future web gate is a product boundary, not a
model approval mechanism:

1. LangGraph writes and checkpoints the pending question before it suspends.
2. The Scientific Worker emits a durable external gate record and stops. It never
   calls `input()`, reads terminal stdin or waits on a terminal session.
3. Only an authenticated person may submit one decision for the exact pending
   gate. The service derives the operator identity, validates the gate version
   and applies existing `coerce_overrides` validation for `revise`.
4. One accepted external decision resumes one LangGraph interrupt. A later gate
   needs a later human decision; no callback or agent answer is reused.
5. Artifact resume and checkpoint continuation remain separate operations. The
   external gate path only continues an existing checkpoint and never calls
   `plan_resume`.

**The CLI is a semantic reference, not the future web contract.** Its validation
and two-resume behaviour remain the reference for a future
`ScientificWorkflowService`, but a Web Worker must not invoke CLI
`--interactive`, call `ask_on_terminal()`, or read terminal stdin. The external
human-gate design is specified in `docs/copilotkit_product_architecture.md`.
Re-implementing scientific validation in a web adapter would create a second
copy that can drift.

**Phase 0 implements none of this.** There is no `ScientificWorkflowService`,
external gate API, worker, web frontend or control tool in this commit.

---

## 10. Golden run evidence fixture

The reviewers read JSON, not matrices. A run's `output.json` files, its
`audit.jsonl`, its `run_metadata.json` and its `report.md` are a few megabytes;
the ~400 MB is `.h5ad`. So a reviewer evaluation can run without the 18 GB of
FASTQ — if there is a fixture.

**A real run directory is not committed.** Run directories contain absolute
paths from the machine that produced them, an endpoint, an operator name,
possibly a hostname, and artifacts that have no business in git.

**Planned (not created by this commit):** `tests/fixtures/golden_run/`, holding
a **cleaned, de-identified evidence fixture** derived from a public-data run.

Requirements on the fixture:

- no `.h5ad`
- no raw FASTQ
- no API key, token or password
- no internal endpoint
- no absolute paths from the producing machine
- no operator identity beyond what a test needs — and where an operator field is
  needed to exercise a code path, a synthetic placeholder
- a **SHA-256 manifest** covering every file in the fixture
- a documented, re-runnable **procedure for regenerating it** from a public
  dataset, so the fixture is reproducible rather than a one-off artefact
- **secret-sentinel and redaction tests**: planted sentinel values that must
  never appear in anything the fixture or the redaction layer emits, following
  the pattern `docs/report_contract.md` already uses for the report's endpoint
  and API key

If the fixture holds, reviewer evaluation becomes a CI check — "did the reviewer
get worse" answerable on every commit.

**This commit designs the fixture and creates none of it.**

---

## 11. Dependencies

The official `deepagents` package (0.7.6 at the time of writing) declares
`langchain>=1.3.14,<2.0.0` and `langchain-core>=1.5.0,<2.0.0`, which the current
pins already satisfy exactly, and `requires-python >=3.11,<4.0`, which
`python=3.11.15` satisfies. Installing it would additionally pull in:

| package | why it arrives |
|---|---|
| `langchain-anthropic` | hard dependency |
| `langchain-google-genai` | hard dependency |
| `langsmith` | hard dependency |
| `packaging` | hard dependency |
| `wcmatch` | hard dependency |

**What this is and is not.** Installing a provider SDK does not send data
anywhere. No code path in this project would call Anthropic or Google, and a
client that is never constructed makes no request. The risk is not "installing
it exfiltrates data". The risks are:

1. **Dependency surface.** Three more packages in the environment that produces
   scientific results, each with its own transitive tree, in a project whose
   `environment.yml` pins versions because `harmonypy` once changed a matrix
   orientation and silently changed the answer.
2. **Misuse of a cloud provider.** With the SDKs present, a model string typo or
   a copied example can reach a cloud endpoint instead of the local one. Today
   that is impossible because the client does not exist.
3. **Tracing switched on by accident.** `langsmith` reads environment variables;
   an inherited `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` in a shell would
   start sending traces. `docs/judge_setup.md` already documents what leaves the
   machine, and this would add a path that document does not cover.

**Decisions:**

- **Phase 0 and Phase 1 do not install `deepagents`.** Nothing in this commit
  changes `environment.yml` or `conda-lock.yml`.
- **Whether it belongs in the main `dcode-scrna` environment is an open
  decision** (section 16).
- **Prefer a separate, throwaway environment for API and model probing.** The
  probe answers "can a local model drive this harness at all" and does not need
  scanpy, Cell Ranger or the lockfile. Contaminating the scientific analysis
  environment to answer a feasibility question is the wrong order.

---

## 12. Model

**Tentative: the reviewers use the same model as the judge, `gpt-oss:120b`, on
the same endpoint.**

The reason is comparability, not quality. `docs/judge_prompt_plan.md` records a
14-case measurement across seven models on this endpoint; `gpt-oss:120b` scored
14/14 and is the current default. Running the reviewers on the same model means
the judge-only control arm (section 13) differs from the reviewer arm in *what
was asked*, not in *what was asked of*. A different model would confound the two.

**Phase 0 calls no model and installs no model package.** This is a recorded
starting point for the first measurement, not a benchmark result.

---

## 13. Evaluation

Following the shape `docs/judge_prompt_plan.md` established, because it is the
shape that has already overturned expectations in this repo.

**Cases.** At least 8 with a planted defect that must be caught, and **at least
6 clean cases that must be left alone**. Six rather than two: in the judge
measurement, three of seven models missed no defect at all and failed purely by
raising false alarms on clean payloads. That is the axis that decides whether a
gate stays worth reading.

**Metrics.**

| metric | how | threshold |
|---|---|---|
| detection rate | planted defects caught at `warn` or `block` | must be strictly better than the judge-only arm |
| false-alarm rate | clean cases producing a non-`info` finding | must be no worse than the judge-only arm; this is a ship gate |
| citation accuracy | every `pointer` resolves and every `value_excerpt` matches the file | 100%; anything less is a hallucinated citation and blocks |
| stability | same input, ≥3 **separate sessions**, 2 runs each; report agreement across sessions | reported, not thresholded |
| wall clock | per review, and as a fraction of the run | reported |
| model cost | total calls and tokens, **including subagent calls** | reported |
| unauthorised modification | file hashes over all of `runs/<id>/**`, plus audit line count and checkpoint mtime, before and after | **must be zero**; this is a test, not a measurement |

Stability is measured **across** sessions, never within one: the judge
measurement found that repeats inside a session are correlated and overstate
stability, and every "2 of 2 consistent" in this project was measured that way.

**Control arms.** Two, and both are required:

1. **judge only** — the same cases with no reviewer, to rule out "the reviewer
   found what the judge had already found".
2. **base-prompt reviewer** — tools but no domain prompt, to establish whether
   the domain prompt or the extra reading is doing the work. This mirrors the
   three-arm design that produced the only measured justification for step
   prompts, where adding *data* changed nothing and the *instruction* changed
   everything.

---

## 14. Phases

Each phase is one revertible step, and none of them changes graph topology.

| phase | produces | done when | revert |
|---|---|---|---|
| **-1 FASTQ baseline** | a public FASTQ golden run and the cleaned fixture of section 10 | `python tests/run_all.py` has no failures; the fixture regenerates from its documented procedure | delete the fixture |
| **0 design** | this file, `prompts/agents/README.md`, and `docs/copilotkit_product_architecture.md` | the product boundaries and open decisions are settled | delete the design files only |
| **1 product read-only foundation** | `ScientificWorkflowService`, read-only FastAPI, AG-UI observation and CopilotKit run views | existing executor tests pass unedited; the product has no mutation endpoint and no terminal-stdin dependency | revert the dedicated product layer; scientific executor semantics remain unchanged |
| **2 MVP** | contracts, read-only inspect tools, redaction, the two reviewers, the coordinator | unauthorised modification is zero; every existing test passes unedited | revert the PR; nothing else in `src/` was touched |
| **3 evaluation** | the case set, the runner, and the measured result written back into this file | both control arms have run and the false-alarm rate has a number | additive only |
| **4 human-approved control** | external web human-gate mode and any control tools under approval, following section 9 | the audit trail shows the person in the loop; both resume semantics still tested and unchanged | revert the control tools |
| **5 remaining reviewers** | the other four domains, **one at a time, each measured** | each new reviewer meets its own false-alarm threshold before it stays | revert individually |
| **6 optional** | literature / ontology tools, de-identified questions only | — | not doing it costs nothing |

Before phase 6, settle point 4 of `docs/judge_prompt_plan.md`'s C-level
discussion: if a deterministic ontology lookup performed by the *step* resolves
most cases, no tool-calling agent is needed for it at all.

---

## 15. Not implemented

Everything in this document. Specifically, none of the following exists in the
repository:

- `src/agent_layer/` — no coordinator, no reviewers, no tools, no contracts, no
  redaction module, no permissions module, no executor adapter
- any `inspect_*` function
- any control tool (`start_workflow`, `resume_from_artifacts`,
  `continue_human_gate`)
- any subagent, of any kind, in any framework
- `tests/agent_layer/`, `evals/`, `tests/fixtures/golden_run/`
- reviewer prompts — `prompts/agents/` contains only the README added here
- the `deepagents` dependency, in `environment.yml`, `conda-lock.yml` or the
  `dcode-scrna` environment
- any measurement of any reviewer; section 13 describes a method, not a result
- the public FASTQ golden run and its evidence fixture

Also not built, and deliberately: the judge's C-level tool calling
(`docs/judge_prompt_plan.md`), which is a separate question about the judge and
is not resolved by anything here.

---

## 16. Open decisions

1. **Does `deepagents` go into the main `dcode-scrna` environment at all?** The
   alternative is to build the coordinator and reviewers directly on the
   `langgraph` and `langchain-openai` already pinned, which adds no dependency
   and keeps every permission decision local, at the cost of writing the
   delegation and approval loop by hand. Section 11 states the risks; this
   decision changes what Phase 2 contains.
2. **Where does the reviewer probe run?** Recommended: a separate throwaway
   environment, so a feasibility question cannot move the scientific lockfile.
3. **Does the deep agent need a checkpointer in v1?** Recommended no — the MVP
   is read-only and has no approval step, so there is nothing to suspend, and
   that keeps exactly one checkpoint semantics in play.
4. **Does the golden-run evidence fixture go into git?** It decides whether
   reviewer evaluation can run in CI. Section 10 states what would have to be
   true of it first.
5. **Can a local model drive a multi-turn tool loop at all?** Unknown, and it is
   the risk most likely to end the MVP: `LocalLLMJudge.judge()` already needs a
   raw-JSON fallback when `with_structured_output` fails, for a *single* call.
   The fallback design if it cannot: give each reviewer its full context in one
   call and drop the tool loop entirely.

---

## 17. Sources

Deep Agents API claims in this document were taken from the official
documentation and checked against the installed `deepagents` source, which was
inspected without being added to this project's environment.

- <https://docs.langchain.com/oss/python/deepagents/overview>
- <https://docs.langchain.com/oss/python/deepagents/quickstart>
- <https://docs.langchain.com/oss/python/deepagents/subagents>
- <https://docs.langchain.com/oss/python/deepagents/human-in-the-loop>
- <https://docs.langchain.com/oss/python/deepagents/permissions>
- <https://reference.langchain.com/python/deepagents/graph/create_deep_agent>
- <https://reference.langchain.com/python/deepagents/middleware/subagents/CompiledSubAgent>
- <https://pypi.org/project/deepagents/>

Two findings from that reading are worth keeping here, because they are the
reason section 6.3 does not rely on the framework's own controls:

1. Filesystem permission rules are evaluated in order and **default to allow**
   when no rule matches, so a deny must be written explicitly.
2. Those rules cover the framework's built-in filesystem tools only. Custom
   tools are not covered by them — which is why the real control is that the
   reviewers have no filesystem access and only a handful of read-only
   functions.

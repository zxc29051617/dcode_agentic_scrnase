# Setting up the judge

Every analysis step is followed by a judge that scores it and can stop the run
at a human gate. The judge is a language model reached over HTTP, and it is the
only part of this pipeline that needs anything outside your machine.

This is how to point it at one, change which one, and check it works before a
run depends on it.

## The two backends

```bash
python -m src.run --input <data> --judge stub    # default: no model at all
python -m src.run --input <data> --judge local   # a real model
```

**`stub` is the default, and it is not a placeholder.** It scores from the step
itself: errors become `fail`, warnings become `warn`, otherwise `pass`. Gates
still fire, the graph still branches, and the whole test suite runs on it — 463
tests, no network. Use it whenever you want the pipeline's behaviour rather than
the model's opinion.

`local` is a misleading name kept for compatibility: it means *any
OpenAI-compatible endpoint*, local or not. `ollama` and `openai-compatible` are
accepted as aliases for it — all three build the same client, which speaks the
OpenAI HTTP API and does not care who is serving it.

    --judge stub                 the deterministic stub, no model, no network
    --judge local                \
    --judge ollama                > the same thing, three spellings
    --judge openai-compatible    /

Anything else is refused by name, by both the CLI and `get_judge`, listing what
is accepted. The two aliases were documented here and rejected by argparse until
the list was generated from one place.

## Configuring an endpoint

Four environment variables, normally set in `.env` (gitignored — your key never
reaches a commit). The file is read at startup:

```bash
cp .env.example .env
```

| variable | what it is |
|---|---|
| `SCRNA_JUDGE_BASE_URL` | the endpoint, **including `/v1`** |
| `SCRNA_JUDGE_MODEL` | the model name as that server spells it |
| `SCRNA_JUDGE_API_KEY` | your key, or any string for servers that ignore it |
| `SCRNA_JUDGE_BACKEND` | `stub`, `local`, `ollama` or `openai-compatible` |

## What wins

Highest first. `.env` fills only variables that are **not already set**, so it
can never undo an export, and neither can undo a flag:

| | backend | model |
|---|---|---|
| 1 | `--judge` | `--judge-model` |
| 2 | `SCRNA_JUDGE_BACKEND` | `SCRNA_JUDGE_MODEL` |
| 3 | the same name in `.env` | the same name in `.env` |
| 4 | `stub` | `qwen2.5:7b-instruct` |

```bash
python -m src.run --input <matrix> --judge local --judge-model gpt-oss:120b
```

Two things this fixes rather than describes. `--judge` used to default to
`"stub"`, which meant the CLI always passed an explicit value and
`SCRNA_JUDGE_BACKEND` could never be reached from the command line — a variable
this guide told people to set, that did nothing. And `.env` was documented here
from the beginning and read by nothing, so following these instructions exactly
gave you the stub with no indication why.

What actually got used is recorded per run in `run_metadata.json` under
`judge_sessions`, and per verdict in the audit log. The recorded value is the
resolved one, not the environment variable — see *What gets recorded about the
judge* below.

### Ollama, on a server (the lab DGX)

```bash
SCRNA_JUDGE_BASE_URL=http://lsbnb-dgx2.iis.sinica.edu.tw:11434/v1
SCRNA_JUDGE_MODEL=gpt-oss:120b
SCRNA_JUDGE_API_KEY=not-needed
```

`/v1` matters. Ollama serves its own API at `/api/…` and an OpenAI-compatible
one at `/v1`; the client here speaks only the second. Ollama ignores the key
but the OpenAI client refuses to start without one, hence the placeholder.

Use the IP (`http://192.168.81.7:11434/v1`) when the hostname will not resolve —
from inside a container, for instance, where `localhost` is the container.

### Ollama, on this machine

```bash
SCRNA_JUDGE_BASE_URL=http://localhost:11434/v1
SCRNA_JUDGE_MODEL=llama3.1:8b
SCRNA_JUDGE_API_KEY=not-needed
```

A model has to fit in your GPU. `gpt-oss:120b` is 65 GB and will not run on a
workstation card; that is what a DGX is for.

### OpenAI

```bash
SCRNA_JUDGE_BASE_URL=https://api.openai.com/v1
SCRNA_JUDGE_MODEL=gpt-4o
SCRNA_JUDGE_API_KEY=sk-...
```

No code changes. The client is `langchain_openai.ChatOpenAI` either way; Ollama
is simply another server speaking the same protocol.

**Read the next section before doing this with unpublished data.**

### Anything else

Azure OpenAI, vLLM, LM Studio, Together, Groq — if it serves
`POST /v1/chat/completions` and accepts a bearer key, it works. Structured
output is attempted first and a raw-JSON parse is kept as the fallback, so
servers without tool calling still return a usable verdict.

## What leaves your machine

A judge call sends the step's output. Concretely:

- cell and gene counts, QC distributions, mitochondrial percentages
- cluster sizes, resolution, which embedding was used
- **gene names** — `find_markers` sends each cluster's top-ranked genes
- assigned cell type labels and their confidence
- file paths, which carry your directory layout

For a public PBMC dataset none of that matters. For unpublished patient data it
is research data and a directory listing, and sending it to a third-party API
is a decision for whoever owns the study, not a configuration detail.

A local or lab-network endpoint sends the same content but keeps it inside the
network. That is the practical difference between the DGX and OpenAI here.

## Check it before a run depends on it

```bash
python scripts/check_judge_endpoint.py
```

Two seconds, and it separates three failures that look alike from inside a
pipeline but are fixed in completely different places:

| it says | what to do |
|---|---|
| cannot reach … | network, or the wrong host from inside a container |
| `<model>` is not on this server | `ollama pull <model>`, or pick from the list it prints |
| structured output failed | that model cannot hold the schema; use another |

It also judges one payload that carries a warning and tells you if the model
called it `pass` — a model that waves warnings through will not stop anything.

Without this you find out several steps into a run, from a langchain traceback.

## Choosing a model

Measured on this project's own payloads, against cases with a known right
answer — three with a planted defect that must not pass, three where the right
answer is known from the data. Twelve observations per model:

| model | correct | median call | verdict |
|---|---|---|---|
| `gpt-oss:20b` | 12/12 | 90.7s | no worse than the 120b, on thin evidence |
| `gpt-oss:120b` | 10/12 | 62.8s | **current default** |
| `medgemma:27b` | 8/12 | 67.6s | passed a 6× doublet rate; do not use |
| `llama3.1:8b` | 6/12 | 6.9s | fast and wrong; do not use |

Two things worth carrying to a different endpoint:

**Smaller was not faster here.** The 13.8 GB model took longer than the 65 GB
one, because the large one stays resident on the GPU and the small one is paged
in per call. On a server that keeps both warm, or on a hosted API, this will
not hold — remeasure rather than assume.

**A domain-tuned model was not better.** `medgemma:27b` is medically
fine-tuned and read these payloads worse than a general model of similar size,
including passing a doublet rate six times its expectation.

The full measurement, and the reason no per-step model override ships, is in
`docs/judge_prompt_plan.md`.

## Per-step models

Optional, and absent by default. `prompts/step_models.json`:

```json
{ "steps": { "find_markers": "gpt-oss:120b", "run_pca": "gpt-oss:20b" } }
```

Steps not named use `SCRNA_JUDGE_MODEL`. A malformed file is ignored rather than
fatal, and a test fails if it names a step that does not exist. No file ships,
because the measurement above found no reason for one on this endpoint.

## Per-step instructions

`prompts/local_judge_base.md` goes to every step. `prompts/steps/<step>.md`, if
it exists, is appended for that step alone. Four steps have one today.

These are not decoration. Asked only to score `cross_check_annotation`, the
judge quoted the flag counts back and never compared the two cell type names in
front of it — 0 of 3 runs found the disagreement. With the step's own
instructions, 3 of 3. Adding the same facts to the payload without the
instruction changed nothing.

`prompts/steps/README.md` has the required shape and how to add one.

## What gets recorded about the judge

A verdict is only as interpretable as the thing that produced it, and on the
measurement above `gpt-oss:120b` scored 12/12 where `llama3.1:8b` scored 6/12 on
the same cases. Two runs of the same data judged by different models are two
different results, so every run records which judge scored it.

`run_metadata.json` grows a `judge_sessions` list:

```json
"judge_sessions": [
  {
    "session_id": "js-00-a3f21c7e04bd",
    "recorded_at": "2026-08-10T08:57:48+00:00",
    "mode": "new",
    "hash_algorithm": "sha256",
    "backend": "local",
    "default_model": "gpt-oss:120b",
    "step_models": { "run_qc_metrics": "small:1b", "run_pca": "gpt-oss:120b" },
    "base_prompt_sha256": "ee2bc398…",
    "step_prompts": {
      "run_qc_metrics": {
        "prompt_sha256": "cb16a10b…",
        "addendum": "run_qc_metrics.md",
        "addendum_sha256": "df912158…"
      },
      "run_pca": { "prompt_sha256": "ee2bc398…", "addendum": null,
                   "addendum_sha256": null }
    },
    "temperature": 0.0,
    "structured_output": "with_structured_output(JudgeResult), raw-JSON fallback",
    "endpoint": "http://lsbnb-dgx2.iis.sinica.edu.tw:11434/v1"
  }
]
```

Four things about it are deliberate:

**The values are the ones that won.** `--judge` beats `SCRNA_JUDGE_BACKEND`, a
constructor argument beats `SCRNA_JUDGE_MODEL`, and a `step_models` entry beats
the default — so the environment says what was *offered*, and the record is
taken from the live judge object, which is the only thing that knows what was
*used*. Note that `get_judge` passes no model, so the CLI cannot set one: the
model comes from `SCRNA_JUDGE_MODEL` or the built-in default.

**The prompt hashes are of the text, not the file.** `prompt_sha256` is taken
over the composed system message the model was actually sent — base plus
addendum, exactly as the judge assembles it — so editing either moves it.
`addendum_sha256` sits alongside so a change can be attributed to the step's own
file rather than to the base prompt every step shares. A step with no addendum
has `prompt_sha256` equal to `base_prompt_sha256`, which is the truth about it.

**It appends, it never overwrites.** A run resumed with `--resume-from` after
changing `SCRNA_JUDGE_MODEL` is judged by two models and its verdicts are a
mixture; overwriting would leave a file claiming the second produced all of
them. `mode` is `new`, `artifact_resume` or `checkpoint_continue`.
`--continue-from` builds its own judge and scores every step after the gate, so
it appends too.

**Nothing secret goes in.** No API key, and any `user:password@` in the endpoint
URL is stripped before it is written. `run_metadata.json` is written beside
results that get shared.

**Every verdict names its session.** The `judge` events in `audit.jsonl` carry
`model` and `judge_session_id`, so "which model said this, under which prompt"
is a lookup rather than a join against timestamps — which stops being
unambiguous the moment a run is resumed twice in the same second. The stub
records `model: null` and `"backend": "stub"`, because "the stub scored this"
and "nobody knows" must not look the same.

The id has two parts and each does a job:

```
js-00-a3f21c7e04bd
   │  └── sha256 of the judge configuration, first 12 hex
   └───── position in this run's judge_sessions
```

The index makes it unique even when a run is resumed under an identical
configuration. The fingerprint makes two sessions *visibly* different when the
prompt moved and the model did not — the case a model name alone cannot tell
apart, and the reason this is not simply a random id. It is recomputable from
what is stored, so the link between a verdict and a configuration can be checked
rather than trusted, and the same fingerprint in two different runs does mean
the same judge configuration.

The format is `js-\d{2,}-[0-9a-f]{12}` — **at least** two digits. The index is
zero-padded so the common cases line up when read, not truncated, so a study
resumed a hundred times reaches `js-100-…` rather than colliding with `js-00-…`.
Anything parsing these should not assume a fixed width.

The fingerprint is taken over what decides a verdict — backend, models, prompt
hashes, temperature, structured-output mode — and **not** over the endpoint.
Serving the same model from a second machine is not a different judge, and
keeping the endpoint out means a session id cannot carry a hostname or anything
embedded beside one.

## When something goes wrong

**Intermittent HTTP 500 from Ollama.** Seen on large payloads under load. The
client retries automatically — the run log shows `Retrying request …` followed
by `200 OK`, and the verdict is real. Six occurred in one full run and none
corrupted a result. Only worry if a step ends with a verdict of `fail` whose
reasons mention the request rather than the data.

**The same payload gets a different verdict.** Expected, up to a point. Runs
inside one session agree with each other; across sessions they can differ, which
is what batched inference does. One case here passed twice, warned twice in
another session, then passed three more times — at temperature 0 on a
byte-identical payload. Cases near a model's pass/warn boundary are where a gate
opens or does not, so treat a single verdict as evidence rather than fact.

**Every step is judged `fail` with a JSON error.** The model is emitting
something that will not parse — often comments or an ellipsis inside the JSON.
It is a prompt problem, not a size problem; `prompts/local_judge_base.md` says
so explicitly for that reason.

**A run takes far longer than the analysis.** It will: 25 judge calls at 60–90s
each dwarf the Scanpy steps on a small object. Use `--judge stub` while
iterating on the analysis and `--judge local` when you want the verdicts.

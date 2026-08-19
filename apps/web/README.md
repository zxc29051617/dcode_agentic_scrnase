# apps/web

Observation UI over `services/gateway`, and — when an analysis controller is
configured — the place an analysis is started and its human gates are
answered. Next.js, React and CopilotKit. See
`docs/copilotkit_product_architecture.md` §3.4 and
`docs/analysis_request_contract.md` for the contracts this implements.

## Two deployments, and the site says which one it is

**Without `ANALYSIS_CONTROLLER_URL`** this app is exactly what it always was:
nothing on it starts a run, resumes a run, or answers a human gate. The
pending-gate card on `/runs/[id]` has no action buttons, `/analysis/new` says
the controller is not configured and points at the CLI, and the header reads
`read-only`.

**With it**, `/analysis/new` can prepare and confirm an analysis request, and
`/runs/[id]` grows an accept / revise / stop control. The header reads
`read + start`. A stale `read-only` label on a site that can start an analysis
would be worse than no label, which is why it is derived rather than written.

What does not change either way: this app never executes anything. It posts to
`services/controller`, which validates and queues; a worker in the
`dcode-scrna` environment is the only thing that runs a workflow.

### What the assistant can never do

There are two assistants with two action sets, and they are never merged.

| | actions | can it start or decide anything? |
|---|---|---|
| run assistant | `list_runs`, `get_run_snapshot`, `get_step_record`, `get_report`, `get_provenance` | no — none of the five writes |
| intake assistant | `list_available_datasets`, `list_available_study_designs`, `prepare_analysis_request`, `get_analysis_request` | no — `prepare_analysis_request` is a preview that creates no run and queues no job |

There is deliberately **no `confirm_analysis_request` action**, and no action
that answers a gate. Confirmation is a POST from a Route Handler reached by a
button; the gate decision is another. A model that can prepare a request and
also confirm it is a model that starts analyses.

The enforcement is that no such function was put within the model's reach, not
the wording of a prompt. `tests/intake.test.ts` fails if one is added.

## Isolated on purpose

Node.js was not present on this machine and is installed into its own conda
environment (`copilotkit-web`, `conda create -n copilotkit-web -c conda-forge
nodejs=22`), never into `dcode-scrna` and never touching this project's
`environment.yml` or `conda-lock.yml`. This app has its own `package.json` and
lockfile, isolated from the scientific dependency set.

```bash
conda activate copilotkit-web
npm install
```

## Configure

```bash
cp .env.local.example .env.local
# set GATEWAY_URL to a running services/gateway instance
```

## The assistant model

Three server-side variables, parsed in `lib/assistantModel.ts`:

| variable | required | notes |
|---|---|---|
| `ASSISTANT_MODEL_BASE_URL` | yes | any OpenAI-compatible endpoint; the `/v1` suffix matters for Ollama |
| `ASSISTANT_MODEL_NAME` | yes | never defaulted; must support tool calling |
| `ASSISTANT_MODEL_API_KEY` | no | blank becomes `not-needed`, as the judge's key does |

Verified working against the lab DGX (`gpt-oss:120b`), which is the same
endpoint and model `.env.example` documents for the judge.

**Unset, the assistant page says "Assistant model is not configured" and
renders no chat box at all.** The runtime still falls back to
`@copilotkit/runtime`'s `ExperimentalEmptyAdapter` so nothing crashes, but the
page never presents an empty adapter as a working assistant. Every other page
— status, timeline, report, provenance — needs no model and works either way.

### Per-visitor model choice, and bringing your own key

Those three variables are the *default*. A visitor can override them for
themselves from the "Assistant settings" panel: pick a different model from
the lab endpoint (listed live by `GET /api/assistant-models`, which asks the
endpoint's own `/v1/models`), or supply their own OpenAI key and model.

`app/api/copilotkit/route.ts` resolves this per request, in strict priority:
a session's OpenAI key, then a session's local-model override on the server's
own endpoint, then the environment default.

The part worth understanding before changing any of it is where the key
lives. Each visitor's config is held in a `Map` keyed by a random session id
(`lib/assistantSession.ts`), and the session id travels in a cookie that is
`httpOnly`, `sameSite=strict`, and `Secure` whenever the request arrived over
HTTPS (`lib/cookieSecurity.ts` — conditional, because a hardcoded `Secure`
silently breaks every session on a plain-HTTP lab deployment).

A single module-level variable would have been much less code and is the
obvious way to write this. It is also wrong on a shared machine: one value
for every concurrent request means the last visitor to type a key spends
*everyone's* budget on it, and no test that runs one request at a time would
ever show that. Nothing here is keyed that way.

Two consequences follow from the design and are not oversights:

- **The key is never persisted.** The store is in memory, so a restart
  empties it. Sessions also expire after 12 idle hours, so a key typed into
  a tab left open for days is not still usable a week later.
- **The key is never returned.** `GET /api/assistant-session` reports only
  *whether* one is set. There is nothing for the settings panel to redisplay,
  which is why changing even the model name requires retyping the key — a
  consequence of the key genuinely not being retrievable, not a UI shortcut.

## Tests

```bash
npm run test:unit          # config parsing, redaction, action shape, operator identity
npm run build              # type-check and produce a bundle
npm run test:bundle        # assert no secret reached .next/static (run after build,
                           # in the same environment the build used)
npm run test:viewer        # Playwright: the embedding viewer and /analysis/new
npm run test:conversation  # real model + real actions + real gateway, end to end
bash tests/live_assistant.sh configured|unconfigured|invalid-endpoint
```

`test:unit` includes `tests/intake.test.ts`, whose load-bearing assertions are
negative: no action can confirm a request, no action can answer a gate, the two
action sets do not overlap, and the operator identity is never taken from a
request body. Each is a boundary that would be silently gone if nothing
asserted on it.

`test:viewer` runs every `*.browser.ts`. The intake tests adapt to the stack
they find: with no controller they assert the page says so and offers no
Confirm button; with one they assert an incomplete request cannot be confirmed
and that the page says why.

`test:conversation` needs a running gateway and a configured model. It drives
the same `READ_ONLY_ACTIONS` the runtime registers, so a passing test cannot
be testing an action the product does not expose.

## Run it

For local development, one command starts both read-only gateway and Next.js:

```bash
npm run dev:stack
```

It starts the gateway, the controller (when `services/controller/.venv`
exists) and Next.js. It deliberately does **not** start the worker: that runs
in `dcode-scrna` because it imports the executor, and a scanpy import does not
belong in the front end's process tree. The script prints the command:

```bash
conda activate dcode-scrna
CONTROLLER_DB=... CONTROLLER_RUNS_ROOT=runs python -m services.controller.worker
```

Set `CONTROLLER_ENABLED=false` to run the read-only stack even with the
controller installed.

It uses `fixtures/synthetic_runs`, gateway port `8010`, controller port `8020`
and web port `3000` by default. Override them without changing the code:

```bash
GATEWAY_RUNS_ROOT=/path/to/runs GATEWAY_PORT=8011 WEB_PORT=3001 npm run dev:stack
```

The gateway remains a separate process and environment; the script only
orchestrates the two services and stops both when either one exits.

For a production-like build:

```bash
npm run build && npm run start -- -p 3000
```

## Pages

```text
/runs                        run inventory
/runs/[id]                   status, workflow timeline, and the pending gate —
                             with accept / revise / stop when a controller is
                             configured, view-only when not
/runs/[id]/report            saved report plus interactive Plotly controls
/runs/[id]/assistant         redirects; the chat is a panel in the shell now
/analysis/new                prepare, review and confirm an analysis request
```

Route handlers:

```text
/api/copilotkit                          the chat runtime; ?mode=intake selects the
                                         intake actions, anything else the read-only five
/api/assistant-session                   GET status (never the key), POST to set, DELETE
/api/assistant-models                    models offered by the configured local endpoint
/api/artifacts/[runId]/[id]              run artifacts, served by opaque id
/api/analysis-requests/preview           validate a draft; executes nothing
/api/analysis-requests/[requestId]       one request's status, for polling
/api/analysis-requests/[requestId]/confirm       human confirmation; queues one job
/api/scientific-runs/[runId]/gates/[gateId]/decision   one human gate answer
```

The last two are the only routes in this app that can change anything. Both
resolve the operator identity server-side and ignore whatever the client sent
— see `lib/operator.ts`, and the note there about why an unset identity in
production is a refusal rather than a fallback.

## Where the API key would live

`lib/gateway.ts` is the only module that knows `GATEWAY_URL`, and
`lib/controller.ts` is the only one that knows `ANALYSIS_CONTROLLER_URL`. Both
start with `import "server-only"` — importing it from a Client Component is a build
error, not a convention someone has to remember. `app/api/copilotkit/route.ts`
is the only module that would read `ASSISTANT_MODEL_API_KEY`, and it is a
Route Handler, which never ships to the browser. Neither name carries the
`NEXT_PUBLIC_` prefix, which is the one thing in Next.js that inlines a value
into client-side JavaScript. `ANALYSIS_CONTROLLER_URL` is on the list
`npm run test:bundle` checks, and it matters more than the gateway's: the
controller is the one service that accepts a POST which can start an analysis,
so a browser that learned its address could talk to it directly and bypass the
route handlers where the operator identity is decided.

# apps/web

Read-only observation UI over `services/gateway`, using Next.js, React and
CopilotKit. See `docs/copilotkit_product_architecture.md` §3.4 for the
contract this implements.

Nothing on this site starts a run, resumes a run, or answers a human gate.
The pending-gate card on `/runs/[id]` has no action buttons — that is Phase 1
by design, not an oversight.

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

## Tests

```bash
npm run test:unit          # config parsing, redaction, read-only action shape
npm run build              # type-check and produce a bundle
npm run test:bundle        # assert no secret reached .next/static (run after build,
                           # in the same environment the build used)
npm run test:conversation  # real model + real actions + real gateway, end to end
bash tests/live_assistant.sh configured|unconfigured|invalid-endpoint
```

`test:conversation` needs a running gateway and a configured model. It drives
the same `READ_ONLY_ACTIONS` the runtime registers, so a passing test cannot
be testing an action the product does not expose.

## Run it

For local development, one command starts both read-only gateway and Next.js:

```bash
npm run dev:stack
```

It uses `fixtures/synthetic_runs`, gateway port `8010`, and web port `3000` by
default. Override them without changing the code:

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
/runs/[id]                   status, pending gate (view-only), workflow timeline
/runs/[id]/report             saved report plus interactive Plotly view/dimension/color controls
/runs/[id]/assistant          CopilotKit chat scoped to read-only actions
```

## Where the API key would live

`lib/gateway.ts` is the only module that knows `GATEWAY_URL`, and it starts
with `import "server-only"` — importing it from a Client Component is a build
error, not a convention someone has to remember. `app/api/copilotkit/route.ts`
is the only module that would read `ASSISTANT_MODEL_API_KEY`, and it is a
Route Handler, which never ships to the browser. Neither name carries the
`NEXT_PUBLIC_` prefix, which is the one thing in Next.js that inlines a value
into client-side JavaScript.

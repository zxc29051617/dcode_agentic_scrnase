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

```bash
npm run build && npm run start -- -p 3000
# or, for development:
npm run dev
```

## Pages

```text
/runs                        run inventory
/runs/[id]                   status, pending gate (view-only), workflow timeline
/runs/[id]/report             saved report, or a stated reason it is absent
/runs/[id]/assistant          CopilotKit chat scoped to read-only actions
```

Route handlers:

```text
/api/copilotkit               the chat runtime; resolves the model per request
/api/assistant-session        GET status (never the key), POST to set, DELETE to clear
/api/assistant-models         models offered by the configured local endpoint
/api/artifacts/[runId]/[id]   run artifacts, served by opaque id
```

## Where the API key would live

`lib/gateway.ts` is the only module that knows `GATEWAY_URL`, and it starts
with `import "server-only"` — importing it from a Client Component is a build
error, not a convention someone has to remember. `app/api/copilotkit/route.ts`
is the only module that would read `ASSISTANT_MODEL_API_KEY`, and it is a
Route Handler, which never ships to the browser. Neither name carries the
`NEXT_PUBLIC_` prefix, which is the one thing in Next.js that inlines a value
into client-side JavaScript.

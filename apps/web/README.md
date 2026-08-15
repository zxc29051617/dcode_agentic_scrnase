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

`ASSISTANT_MODEL_BASE_URL` / `ASSISTANT_MODEL_API_KEY` are left unset in
Phase 1: no local model endpoint or API key was available to wire in when this
was built (no Ollama running, no project `.env`, nothing provided). Unset,
`app/api/copilotkit/route.ts` falls back to `@copilotkit/runtime`'s own
`ExperimentalEmptyAdapter`, so every page and every read-only action still
runs against real gateway data — there is simply no model in the loop yet to
carry on a conversation about it. Setting those two variables points the
assistant at a real OpenAI-compatible endpoint once one is approved, per
`docs/copilotkit_product_architecture.md`'s "model-egress policy" item.

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

## Where the API key would live

`lib/gateway.ts` is the only module that knows `GATEWAY_URL`, and it starts
with `import "server-only"` — importing it from a Client Component is a build
error, not a convention someone has to remember. `app/api/copilotkit/route.ts`
is the only module that would read `ASSISTANT_MODEL_API_KEY`, and it is a
Route Handler, which never ships to the browser. Neither name carries the
`NEXT_PUBLIC_` prefix, which is the one thing in Next.js that inlines a value
into client-side JavaScript.

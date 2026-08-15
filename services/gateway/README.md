# services/gateway

Read-only FastAPI projection over `runs/<id>/` directories. See
`docs/copilotkit_product_architecture.md` §3.2 for the contract this
implements.

There is no route here that writes a file, starts `src.run`, opens a
checkpoint, or answers a gate. This service never imports `src/`; it rebuilds
everything it serves from `audit.jsonl`, `run_metadata.json` and per-step
`output.json` files, fresh on every request.

## Isolated on purpose

This service has its own `requirements.txt` and its own virtualenv. It is
never installed into `dcode-scrna`, and nothing here edits `environment.yml`
or `conda-lock.yml`.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

(`python3.11` was used because this machine's `python3.12` lacks the
`python3-venv` system package and there is no sudo access here to install it;
any Python >=3.11 works.)

## Run it

```bash
GATEWAY_RUNS_ROOT=/path/to/runs .venv/bin/uvicorn app.main:app --port 8010
```

Against the synthetic fixture:

```bash
GATEWAY_RUNS_ROOT=../../fixtures/synthetic_runs .venv/bin/uvicorn app.main:app --port 8010
```

## Test it

```bash
.venv/bin/pytest tests/ -v
```

## Endpoints

All `GET`, all read-only:

```text
GET /healthz
GET /v1/scientific-runs
GET /v1/scientific-runs/{id}
GET /v1/scientific-runs/{id}/steps
GET /v1/scientific-runs/{id}/report
GET /v1/scientific-runs/{id}/provenance
```

`app/read_model.py` is the only module that touches the filesystem.
`resolve_run_dir` rejects any `id` that is not a direct child of
`GATEWAY_RUNS_ROOT` — by regex first (no `/`, no `..`), then by resolving the
path and checking it is still inside the root (catches a symlink escape the
regex cannot see). Both failure modes return the same 404 an unknown run id
would, on purpose.

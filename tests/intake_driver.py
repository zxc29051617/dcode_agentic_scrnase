"""One controller call, run inside the controller's own virtualenv.

`test_web_intake_flow.py` spawns this. It is a separate file, run by a
different interpreter, because that is what the product actually is: the
controller has FastAPI and no scanpy, the worker has scanpy and no FastAPI, and
they share exactly one thing — the SQLite store. A test that imported both into
one process would be testing an arrangement that does not exist.

Every mode prints one JSON object on stdout prefixed with `RESULT `.

    python tests/intake_driver.py preview <db> <runs_root> <catalog> <roots> <payload_json>
    python tests/intake_driver.py confirm <db> <runs_root> <catalog> <roots> <request_id> <digest>
    python tests/intake_driver.py gate    <db> <runs_root> <catalog> <roots> <run_id>
    python tests/intake_driver.py decide  <db> <runs_root> <catalog> <roots> <run_id> <payload_json>
    python tests/intake_driver.py status  <db> <runs_root> <catalog> <roots> <request_id>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_ROOT = REPO_ROOT / "services" / "controller"
for entry in (str(REPO_ROOT), str(CONTROLLER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _emit(**fields: object) -> None:
    print("RESULT " + json.dumps(fields, default=str))


def _client(db: str, runs_root: str, catalog: str, roots: str):
    os.environ["CONTROLLER_DB"] = db
    os.environ["CONTROLLER_RUNS_ROOT"] = runs_root
    os.environ["CONTROLLER_CATALOG"] = catalog
    os.environ["CONTROLLER_DATA_ROOTS"] = roots
    from fastapi.testclient import TestClient

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    return TestClient(app)


def main(argv: list[str]) -> int:
    mode, db, runs_root, catalog, roots, *rest = argv
    client = _client(db, runs_root, catalog, roots)

    if mode == "preview":
        response = client.post("/v1/analysis-requests/preview", json=json.loads(rest[0]))
        _emit(code=response.status_code, body=response.json())
    elif mode == "confirm":
        request_id, digest = rest
        response = client.post(
            f"/v1/analysis-requests/{request_id}/confirm",
            json={"config_digest": digest, "operator_id": "integration-operator"},
        )
        _emit(code=response.status_code, body=response.json())
    elif mode == "gate":
        response = client.get(f"/v1/scientific-runs/{rest[0]}/gate")
        _emit(code=response.status_code, body=response.json())
    elif mode == "decide":
        run_id, payload = rest
        parsed = json.loads(payload)
        state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
        response = client.post(
            f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
            json={**parsed, "expected_generation": state["generation"]},
        )
        _emit(code=response.status_code, body=response.json(), gate=state)
    elif mode == "status":
        response = client.get(f"/v1/analysis-requests/{rest[0]}/status")
        _emit(code=response.status_code, body=response.json())
    else:
        _emit(code=400, body={"detail": f"unknown mode {mode!r}"})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

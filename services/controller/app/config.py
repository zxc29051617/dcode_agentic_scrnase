"""What the controller is allowed to touch, and where it keeps its own state.

Three roots, and they are three different trust levels:

`CONTROLLER_DB` is the controller's own store. It is deliberately *not* inside
`runs/<scientific_run_id>/` — only the scientific worker writes there, and a
controller database in a run directory would make the controller a writer of
scientific run storage by accident of layout.

`CONTROLLER_RUNS_ROOT` is read-only to this service. The controller reads a
run's `audit.jsonl` to find which gate is pending, and never opens a file there
for writing. That is a property of the code (`app/gates.py` has no write path),
not of a filesystem permission.

`CONTROLLER_DATA_ROOTS` is the allowlist that decides which parts of the machine
an analysis request may ever name. Nothing outside it is reachable from a
conversation, a browser or a model, however the path is spelled.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(
        self,
        *,
        db_path: str | None = None,
        runs_root: str | None = None,
        data_roots: str | None = None,
        catalog_path: str | None = None,
    ) -> None:
        raw_db = db_path or os.environ.get("CONTROLLER_DB")
        if not raw_db:
            raise RuntimeError(
                "CONTROLLER_DB is not set. The controller refuses to guess where to "
                "keep its database — and it must not be inside a run directory."
            )
        self.db_path = Path(raw_db).expanduser().resolve()

        raw_runs = runs_root or os.environ.get("CONTROLLER_RUNS_ROOT")
        if not raw_runs:
            raise RuntimeError("CONTROLLER_RUNS_ROOT is not set.")
        self.runs_root = Path(raw_runs).expanduser().resolve()
        if not self.runs_root.is_dir():
            raise RuntimeError(f"CONTROLLER_RUNS_ROOT is not a directory: {self.runs_root}")

        if self.db_path.is_relative_to(self.runs_root):
            # Layout, not permissions, is what keeps the controller out of
            # scientific run storage. A database under runs/ would be this
            # service writing there on every request.
            raise RuntimeError(
                f"CONTROLLER_DB ({self.db_path}) is inside CONTROLLER_RUNS_ROOT "
                f"({self.runs_root}). Only the scientific worker writes under runs/."
            )

        raw_roots = data_roots if data_roots is not None else os.environ.get("CONTROLLER_DATA_ROOTS", "")
        roots: list[Path] = []
        for entry in raw_roots.split(os.pathsep):
            entry = entry.strip()
            if not entry:
                continue
            root = Path(entry).expanduser().resolve()
            if root.is_dir():
                roots.append(root)
        self.data_roots = tuple(roots)

        raw_catalog = catalog_path if catalog_path is not None else os.environ.get("CONTROLLER_CATALOG", "")
        self.catalog_path = Path(raw_catalog).expanduser().resolve() if raw_catalog else None

        #: How the worker is expected to score steps. Passed to the executor
        #: unchanged; `stub` needs no endpoint and is the local default.
        self.judge_backend = os.environ.get("CONTROLLER_JUDGE_BACKEND") or None
        self.judge_model = os.environ.get("CONTROLLER_JUDGE_MODEL") or None


@lru_cache
def get_settings() -> Settings:
    return Settings()

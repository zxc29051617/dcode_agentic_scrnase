"""Where the gateway is allowed to read from, and nothing else.

`RUNS_ROOT` names the one directory this service will ever open a file under.
There is no second path anywhere in this service that reaches the filesystem —
every read goes through `read_model.py`, and every read in `read_model.py`
goes through `resolve_run_dir`, which refuses to leave this root.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(self, runs_root: str | None = None) -> None:
        raw = runs_root or os.environ.get("GATEWAY_RUNS_ROOT")
        if not raw:
            raise RuntimeError(
                "GATEWAY_RUNS_ROOT is not set. The gateway refuses to guess a "
                "runs directory — point it at one explicitly."
            )
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError(f"GATEWAY_RUNS_ROOT does not exist or is not a directory: {root}")
        self.runs_root = root


@lru_cache
def get_settings() -> Settings:
    return Settings()

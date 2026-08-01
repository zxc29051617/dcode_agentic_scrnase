"""Append-only audit log.

Every node writes one JSON line per event so a run can be replayed and audited
without reading the analysis artifacts themselves.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    """Best-effort coercion so an unserializable artifact never kills a run."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


class AuditLog:
    """JSONL writer scoped to one run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{key: _jsonable(value) for key, value in fields.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

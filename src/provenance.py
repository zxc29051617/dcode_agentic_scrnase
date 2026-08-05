"""Append-only audit log, and the run metadata needed to reproduce a run.

Every node writes one JSON line per event so a run can be replayed and audited
without reading the analysis artifacts themselves.

`capture_run_metadata` records the other half: what the code and environment
*were* when the analysis ran. It is written once at run start rather than
collected when the report is built, because those are not the same moment — a
report regenerated next month runs against whatever is installed then, and
would describe an environment that never produced these results.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Everything whose version can change a number in the output. Recording the
#: pipeline's own git commit is not enough: identical code on a different
#: scanpy produces different clusters.
TRACKED_PACKAGES = (
    "scanpy",
    "anndata",
    "numpy",
    "scipy",
    "pandas",
    "scikit-learn",
    "umap-learn",
    "harmonypy",
    "celltypist",
    "scrublet",
    "leidenalg",
    "igraph",
    "scikit-misc",
    "matplotlib",
    "langgraph",
)


def package_versions(names: tuple[str, ...] = TRACKED_PACKAGES) -> dict[str, str | None]:
    """Installed version of each tracked package, or None when absent."""
    from importlib.metadata import PackageNotFoundError, version

    found: dict[str, str | None] = {}
    for name in names:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
        except Exception:  # noqa: BLE001 - a metadata quirk must not kill a run
            found[name] = None
    return found


def _git(*args: str, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_state(root: Path | None = None) -> dict[str, Any]:
    """Commit, branch, and whether the tree had uncommitted changes.

    `dirty` matters as much as the commit: the same hash with edited files on
    top is not the same code, and a report that cites only the commit would be
    quietly wrong about what ran.
    """
    cwd = Path(root or Path(__file__).resolve().parent.parent)
    status = _git("status", "--porcelain", cwd=cwd)
    return {
        "commit": _git("rev-parse", "HEAD", cwd=cwd),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd),
        "dirty": None if status is None else bool(status),
    }


def file_digest(path: str | Path | None) -> str | None:
    """SHA-256 of a file, or None if it is missing or unreadable.

    Used for things named by path or filename — a reference directory, a
    CellTypist model — where the name staying the same does not mean the
    contents did.
    """
    if not path:
        return None
    target = Path(path).expanduser()
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def config_digest(config: dict[str, Any]) -> str:
    """Stable hash of the resolved config, so two runs can be compared."""
    payload = json.dumps(config, sort_keys=True, default=repr, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_run_metadata(
    *,
    run_id: str,
    config: dict[str, Any] | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Everything needed to say what produced a run, gathered at run start."""
    resolved = dict(config or {})
    return {
        "run_id": run_id,
        "runtime": {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "hostname": platform.node(),
        },
        "source": {
            **git_state(),
            "command": list(command) if command is not None else list(sys.argv),
            "config_sha256": config_digest(resolved),
        },
        "packages": package_versions(),
        "seeds": {"random_state": resolved.get("random_state", 0)},
    }


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

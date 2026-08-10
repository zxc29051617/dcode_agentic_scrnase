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


def comparable_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """The config as it survives a round trip through `run_metadata.json`.

    A resume compares what is on disk against what is in memory, and those are
    not the same objects: a tuple comes back a list, and anything exotic comes
    back as its `repr`. Both sides go through this so the comparison is about
    values that actually differ rather than about how JSON stores them.
    """
    return json.loads(json.dumps(dict(config or {}), sort_keys=True, default=repr))


def input_digest(input_bundle: dict[str, Any] | None) -> str | None:
    """Content hash of every file the run was pointed at, or None if it cannot be taken.

    Returns None when the bundle names nothing, or names something that is no
    longer there — both mean "cannot be compared", which a resume has to read as
    "cannot be trusted" rather than as "unchanged".

    **Bytes, not size and mtime.** The cheap check was tried first and misses
    the edit that matters: a matrix regenerated with one value changed need not
    change size at all, and mtime says nothing about content. The cost is one
    pass over the input, paid once when a resume is planned, against a Cell
    Ranger count that takes twenty minutes and a wrong answer that takes a paper.

    It hashes the file as stored, which for a `.gz` includes the modification
    time gzip writes into its own header — measured, not assumed. So
    re-compressing an input that has not changed does read as a change and does
    force a rerun. That is a false positive in the safe direction, and the
    alternative — decompressing every input to compare what is inside — is a
    per-format special case for a situation real data does not often produce.
    """
    paths = (input_bundle or {}).get("paths") or []
    if not paths:
        return None

    entries: list[list[str]] = []
    for raw in sorted(str(path) for path in paths):
        root = Path(raw).expanduser()
        if not root.exists():
            return None
        if root.is_file():
            files = [(root.name, root)]
        else:
            files = [
                (str(child.relative_to(root)), child)
                for child in sorted(root.rglob("*"))
                if child.is_file()
            ]
        for name, target in files:
            digest = file_digest(target)
            if digest is None:
                return None
            entries.append([f"{root.name}/{name}", digest])

    payload = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_run_metadata(
    *,
    run_id: str,
    config: dict[str, Any] | None = None,
    command: list[str] | None = None,
    input_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything needed to say what produced a run, gathered at run start.

    `config` is recorded in full alongside its hash, and `input_digest` beside
    it. The hash alone can only answer "did anything change?", which is the
    question that forces a resume to throw away the whole directory. Keeping the
    values themselves lets it answer "*what* changed", and therefore which step
    is the first that can no longer be trusted.
    """
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
            "config": comparable_config(resolved),
            "config_sha256": config_digest(resolved),
            "input_digest": input_digest(input_bundle),
        },
        "packages": package_versions(),
        "seeds": {"random_state": resolved.get("random_state", 0)},
    }


def record_revision(
    metadata_path: str | Path,
    *,
    step: str,
    overrides: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    """Record that an operator changed the config mid-run, and re-hash it.

    Two separate jobs, and the second is the one that matters for correctness.

    The `revisions` list is the readable record: which gate, which step, what
    was set. It is appended to, never rewritten, so a run revised twice says so.

    `source.config_sha256` is rewritten, and has to be. `resumable_steps`
    trusts a run directory only when the config hash recorded there matches the
    config being resumed with — so leaving it at the hash of the config the run
    *started* with would mean a later `--resume-from`, given the original
    command line, matched and handed back artifacts computed from the revised
    values. That is two analyses in one report, which is the exact failure the
    hash check exists to prevent.

    Everything else the file records — the git commit, the package versions, the
    start time — is left alone. A revision changes what was asked for, not what
    was installed or when the run began.
    """
    path = Path(metadata_path)
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Losing the record must never lose the decision that just happened;
        # the audit log has it either way. A resume then fails closed, because
        # an unreadable hash never matches.
        return False

    revisions = metadata.get("revisions")
    if not isinstance(revisions, list):
        revisions = []
    revisions.append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step,
        "overrides": {key: _jsonable(value) for key, value in overrides.items()},
    })
    metadata["revisions"] = revisions
    source = metadata.setdefault("source", {})
    # Both, and for different readers: the hash is what a whole-directory check
    # compares, the config itself is what a per-step resume diffs to find the
    # earliest step it invalidated. Updating one and not the other would leave
    # them describing different runs.
    source["config"] = comparable_config(config)
    source["config_sha256"] = config_digest(config)

    try:
        path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return False
    return True


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

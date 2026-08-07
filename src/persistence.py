"""Pausing a run, and picking one up again.

Two separate problems, solved separately, because conflating them is what puts
a pipeline in the position of believing two contradictory things at once.

**Pausing** (`make_checkpointer`, `thread_config`) uses LangGraph's own
mechanism: `interrupt()` suspends inside a superstep and `Command(resume=...)`
answers it. That needs a checkpointer, and the in-memory one is enough —
pausing is about waiting for a person who is already there.

**Resuming** (`resumable_steps`) does not use the checkpointer at all. It reads
what is on disk. A step is done when its artifacts exist and the config that
produced them still matches, which makes the artifact directory the single
source of truth about progress.

A persistent checkpointer would answer both, and was deliberately not used. It
would introduce a second record of what has run, and the two can disagree:
delete a `.h5ad`, or rerun one step through its standalone CLI — which this
project supports for all 44 of them — and the checkpoint still insists the step
is complete. The failure is silent and produces a report describing a run that
did not happen. It is also coupled to graph topology, and two steps are still
unimplemented.

The cost of that choice is precise and worth stating: a gate that is *waiting*
cannot be resumed in a new process, because a pending question is not an
artifact. Resuming re-runs from the last completed step and asks again. If that
ever becomes expensive, `make_checkpointer` is the one function that has to
change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Written beside each step's artifacts. The step's own return value, which is
#: what the next step reads out of state — so a resumed run can rebuild state
#: without having stored state itself.
STEP_OUTPUT_NAME = "output.json"

#: Keys whose value is a path that must still exist for the step to count as
#: done. A recorded summary is worthless if the object it describes is gone.
ARTIFACT_PATH_KEYS = ("adata_path", "marker_table_path", "cell_flags_path")

DEFAULT_RECURSION_LIMIT = 150


# --- pausing ------------------------------------------------------------------


def make_checkpointer(kind: str = "memory") -> Any:
    """A checkpointer for `interrupt()`, or None to keep the current behaviour.

    `memory` is deliberate rather than provisional: see the module docstring.
    `none` is what every existing caller gets, so a run that does not ask to
    pause behaves exactly as it did before this module existed.
    """
    if kind in (None, "none"):
        return None
    if kind == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    raise ValueError(f"unknown checkpointer: {kind!r} (expected 'memory' or 'none')")


def thread_config(run_id: str, *, recursion_limit: int = DEFAULT_RECURSION_LIMIT,
                  checkpointer: Any = None) -> dict[str, Any]:
    """The `invoke` config for a run.

    LangGraph needs a `thread_id` to file checkpoints under, and `run_id` is
    already unique per run — inventing a second identifier would just be one
    more thing that can disagree. Without a checkpointer the key is omitted, so
    the config is byte-for-byte what it was before.
    """
    config: dict[str, Any] = {"recursion_limit": recursion_limit}
    if checkpointer is not None:
        config["configurable"] = {"thread_id": run_id}
    return config


# --- resuming -------------------------------------------------------------------


def plain_python(value: Any) -> Any:
    """Replace numpy scalars and arrays with the built-ins they wrap.

    `numpy.float64` subclasses `float`, so `isinstance(x, float)` is True and
    `json.dumps` accepts it — which is why this went unnoticed. The
    checkpointer serialises with msgpack, which does not, so a single numpy
    scalar left in a step's output makes the whole run unpausable, and it fails
    at the gate rather than in the step that produced it.

    Detected by duck typing rather than importing numpy: this module is on the
    import path of every run, including ones that never touch a matrix.
    """
    if isinstance(value, dict):
        return {key: plain_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_python(item) for item in value]
    if hasattr(value, "dtype") and hasattr(value, "tolist"):
        return plain_python(value.tolist())
    return value


def step_output_path(run_dir: str | Path, step: str) -> Path:
    return Path(run_dir) / step / STEP_OUTPUT_NAME


def write_step_output(run_dir: str | Path, step: str, output: dict[str, Any]) -> str | None:
    """Record a step's return value next to the artifacts it describes.

    State is not persisted anywhere, so without this a resumed run would know
    which steps ran but nothing about what they produced — no thresholds, no
    summaries, no paths for the next step to read. Written per step rather than
    as one file so a half-finished run cannot corrupt the record of the steps
    that did finish.
    """
    path = step_output_path(run_dir, step)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        # Losing the resume record must never lose the step that just ran.
        return None
    return str(path)


def read_step_output(run_dir: str | Path, step: str) -> dict[str, Any] | None:
    path = step_output_path(run_dir, step)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def artifacts_present(output: dict[str, Any]) -> bool:
    """Does everything this step said it wrote still exist?

    The check that stops a resumed run from trusting a record of a file that
    has since been deleted, moved, or never finished being written.
    """
    for key in ARTIFACT_PATH_KEYS:
        recorded = output.get(key)
        if recorded and not Path(str(recorded)).exists():
            return False
    return True


def recorded_config_hash(run_dir: str | Path) -> str | None:
    """The config hash of the run that produced this directory."""
    try:
        metadata = json.loads((Path(run_dir) / "run_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (metadata.get("source") or {}).get("config_sha256")


def resumable_steps(run_dir: str | Path, config_hash: str | None = None) -> dict[str, Any]:
    """`{step: recorded output}` for every step that can be trusted as done.

    A step qualifies only if it recorded an output *and* the files that output
    names are still there. A changed config disqualifies the whole directory
    rather than individual steps: thresholds set at one step change what every
    later step should produce, so a partial match is not a safe thing to build
    on.
    """
    root = Path(run_dir)
    if not root.is_dir():
        return {}
    if config_hash is not None:
        # Fails closed. An unreadable or missing `run_metadata.json` means the
        # config cannot be compared, not that it matches — skipping the check
        # there would resume onto any config at all and mix the results of two
        # different analyses, which is the one thing this guard exists for.
        if recorded_config_hash(root) != config_hash:
            return {}

    found: dict[str, Any] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        output = read_step_output(root, child.name)
        if output is None or not artifacts_present(output):
            continue
        found[child.name] = output
    return found

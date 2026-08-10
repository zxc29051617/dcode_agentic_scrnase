"""Pausing a run, and picking one up again.

Two separate problems, solved separately, because conflating them is what puts
a pipeline in the position of believing two contradictory things at once.

**Pausing** (`make_checkpointer`, `thread_config`) uses LangGraph's own
mechanism: `interrupt()` suspends inside a superstep and `Command(resume=...)`
answers it. That needs a checkpointer, and the in-memory one is enough —
pausing is about waiting for a person who is already there.

**Resuming** (`plan_resume`) does not use the checkpointer at all. It reads what
is on disk, which makes the artifact directory the single source of truth about
progress.

What it reads is a *cut*, not a yes/no for the whole directory. The first
version compared one hash for the run: if the config had moved at all, nothing
was reusable. Safe, and almost always wasteful — changing `celltypist_model`
discarded the PCA, the Harmony correction, the clustering and the markers, none
of which read it. `plan_resume` instead finds the earliest step that the
difference could have changed, recomputes from there, and reuses what came
before it after verifying each step individually. `StepSpec.config_keys` is
what makes "could have changed" answerable per step.

It fails closed everywhere it cannot tell: metadata that is missing, corrupt, or
too old to record a config; an input path that has gone; a step whose outcome is
not in the audit log; a status that is not `ok`; recorded errors; a recorded
file that is no longer there. Any of those and the step is recomputed, along
with everything after it — because whatever came next was computed from it.

A persistent checkpointer would answer both, and was deliberately not used. It
would introduce a second record of what has run, and the two can disagree:
delete a `.h5ad`, or rerun one step through its standalone CLI — which this
project supports for all 26 of them — and the checkpoint still insists the step
is complete. The failure is silent and produces a report describing a run that
did not happen. It is also coupled to graph topology.

The cost of that choice is precise and worth stating: a gate that is *waiting*
cannot be resumed in a new process, because a pending question is not an
artifact. Resuming re-runs from the last completed step and asks again. If that
ever becomes expensive, `make_checkpointer` is the one function that has to
change.

## The other cost is disk
Every step writing its own AnnData is what makes resuming possible, and it is
also what makes a run expensive to keep: the two-sample PBMC test costs about
410 MB, nearly all of it `.h5ad`, and a study with more libraries or a deeper
matrix scales from there. Nothing here deletes anything — which run is still
worth keeping is a judgement about the work, not about the bytes.

`scripts/run_disk_usage.sh` reports what is there and which runs finished.
Deleting a run's `adata.h5ad` files keeps the report, figures, markers and
provenance while giving up the ability to resume it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import AuditLog, comparable_config, input_digest
from .registry import REGISTRY, earliest_step_reading

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


def read_run_metadata(run_dir: str | Path) -> dict[str, Any] | None:
    """The run's recorded metadata, or None when it cannot be read."""
    try:
        return json.loads((Path(run_dir) / "run_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recorded_config_hash(run_dir: str | Path) -> str | None:
    """The config hash of the run that produced this directory."""
    metadata = read_run_metadata(run_dir)
    return (metadata.get("source") or {}).get("config_sha256") if metadata else None


def final_step_status(run_dir: str | Path) -> dict[str, str]:
    """`{step: status}` from the audit log, latest event per step wins.

    Status is read from the audit log rather than from a file beside the
    artifacts, because the audit log is already the record of what happened and
    a second copy is a second thing that can disagree with it. It also gets the
    ordering right for free: a step run twice — a `revise` — is judged on its
    last outcome, not its first.
    """
    statuses: dict[str, str] = {}
    for record in AuditLog(Path(run_dir) / "audit.jsonl").read():
        event = record.get("event")
        if event == "step_end":
            statuses[str(record.get("step"))] = str(record.get("status"))
        elif event == "step_skipped":
            # A skip means an earlier run's result was reused, so whatever that
            # run recorded still stands. Do not overwrite it.
            statuses.setdefault(str(record.get("step")), "skipped")
    return statuses


@dataclass(frozen=True)
class ResumePlan:
    """What a resumed run may reuse, what it must recompute, and why.

    `reasons` exists because this is a decision a person has to be able to
    audit. A resume that silently reuses eighteen steps and a resume that
    silently reuses none look identical from the outside, and the difference
    between them is whether the report describes one analysis or two.
    """

    reusable: dict[str, Any]
    rerun_from: str | None
    reasons: list[str]

    @property
    def blocked(self) -> bool:
        """True when nothing at all can be reused."""
        return not self.reusable


def _step_is_trustworthy(
    run_dir: Path, step: str, status: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """The recorded output of `step`, or None and the reason it cannot be used.

    Four ways a recorded step fails to be a result, and each is silent if it is
    not checked for. `scaffold` and `error` wrote an empty output and no
    artifacts. A step that recorded errors alongside its output ran, but ran
    against something that was not there. And a recorded path that no longer
    exists is the case where the record outlived the thing it describes.
    """
    output = read_step_output(run_dir, step)
    if output is None:
        return None, None  # never ran on this route; not a failure, nothing to say
    if status is None:
        return None, f"{step}: no step_end in the audit log, so its outcome is unknown"
    if status not in {"ok", "skipped"}:
        return None, f"{step}: recorded status {status!r}, not a completed result"
    if output.get("errors"):
        return None, f"{step}: completed with errors ({output['errors'][0]})"
    if not artifacts_present(output):
        return None, f"{step}: a file it recorded is no longer on disk"
    return output, None


def plan_resume(
    run_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    input_bundle: dict[str, Any] | None = None,
) -> ResumePlan:
    """Decide, step by step, what a resumed run is allowed to keep.

    The old rule was one comparison for the whole directory: if the config hash
    moved at all, nothing was reusable. That is safe and almost always wasteful
    — changing `celltypist_model` threw away the PCA, the clustering and the
    markers, none of which read it.

    The rule now is a cut. Find the earliest step that the difference could have
    changed; everything from there on is recomputed, everything before it is
    reused if it can be verified. Three things can move the cut earlier:

      - the input data changed, which invalidates the first step
      - a config key changed, which invalidates the earliest step that reads it
        (`registry.earliest_step_reading`, unrecognised keys count as the first)
      - a step that did run cannot be verified, which invalidates itself and
        therefore everything after it

    It fails closed at every step where it cannot tell. Metadata that is
    missing, unreadable, or written before this check existed carries no
    recorded config to diff, so nothing is reused — an old run directory is
    recomputed rather than half-trusted.

    A step with no recorded output at all is *not* a failure and does not move
    the cut: on the filtered route `load_raw_counts` never runs, and its absence
    says nothing about `merge_samples`.
    """
    root = Path(run_dir)
    if not root.is_dir():
        return ResumePlan({}, None, [f"{root} is not a run directory"])

    metadata = read_run_metadata(root)
    source = (metadata or {}).get("source") or {}
    recorded_config = source.get("config")
    if metadata is None or not isinstance(recorded_config, dict):
        return ResumePlan({}, None, [
            "run_metadata.json is missing, unreadable, or records no config; "
            "nothing can be verified, so nothing is reused"
        ])

    reasons: list[str] = []
    order = list(REGISTRY)
    cut: str | None = None

    # --- did the data itself move? -------------------------------------------
    recorded_input = source.get("input_digest")
    current_input = input_digest(input_bundle)
    if recorded_input is None or current_input is None:
        cut = order[0]
        reasons.append(
            "the input data cannot be compared (nothing recorded, or a path is gone); "
            "re-running from the first step"
        )
    elif recorded_input != current_input:
        cut = order[0]
        reasons.append("the input data changed; re-running from the first step")

    # --- did the settings move? ----------------------------------------------
    current_config = comparable_config(config)
    baseline = comparable_config(recorded_config)
    changed = sorted(
        key for key in set(baseline) | set(current_config)
        if baseline.get(key) != current_config.get(key)
    )
    if changed:
        earliest, owner = earliest_step_reading(changed)
        for key in changed:
            reasons.append(
                f"{key}: {baseline.get(key)!r} -> {current_config.get(key)!r}, "
                f"first read by {owner[key]}"
            )
        if earliest is not None and (cut is None or order.index(earliest) < order.index(cut)):
            cut = earliest

    if cut is not None:
        reasons.append(f"re-running from {cut} onward")

    # --- verify everything before the cut ------------------------------------
    statuses = final_step_status(root)
    reusable: dict[str, Any] = {}
    for index, step in enumerate(order):
        if cut is not None and index >= order.index(cut):
            continue
        output, problem = _step_is_trustworthy(root, step, statuses.get(step))
        if output is not None:
            reusable[step] = output
            continue
        if problem is not None:
            # A step that ran and cannot be verified invalidates itself and
            # everything after it: whatever comes next was computed from what
            # this one produced.
            cut = step
            reasons.append(problem)
            reasons.append(f"re-running from {step} onward")
            for later in order[index:]:
                reusable.pop(later, None)
            break

    if not reasons:
        reasons.append("nothing changed; every verified step is reused")
    return ResumePlan(reusable, cut, reasons)

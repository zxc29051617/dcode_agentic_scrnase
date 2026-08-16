"""Everything the gateway knows, rebuilt from files on disk, every request.

No caching across requests, no filesystem write, no import of `src/`. This
service does not run the scientific package — it reads the same three sources
`docs/deep_agents_architecture.md` names as the record of a run: audit.jsonl,
run_metadata.json and the per-step output.json files it writes beside them.
That design choice, not a permission check, is what makes this service unable
to start a workflow, answer a gate, or invalidate a checkpoint: there is no
code path here that does any of those things.

`docs/copilotkit_product_architecture.md` §3.2 calls this class of endpoint a
"bounded UI projection, not raw WorkflowState... or complete artifact
dictionaries" — `get_provenance` in particular returns a named subset of
`run_metadata.json`, not the file verbatim, because a real run's `command`
field can carry a local absolute path and this service must not repeat one
into a browser response.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: No `/`, no `..`, no leading dot — a run id that cannot climb out of
#: `runs_root` by construction, before any path resolution happens at all.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def resolve_run_dir(runs_root: Path, run_id: str) -> Path | None:
    """The run's directory, or `None` if `run_id` is not a real child of `runs_root`.

    Two independent checks, not one. The regex rejects `/` and `..` outright,
    so `../../etc` never reaches `Path.resolve()`. The `relative_to` check
    catches what the regex cannot: a symlink inside `runs_root` pointing
    outside it. Either check failing is reported identically to the caller —
    a run that does not exist — so a path-traversal attempt learns nothing
    about the filesystem it was refused.
    """
    if not RUN_ID_RE.match(run_id):
        return None
    root = runs_root.resolve()
    candidate = (root / run_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate == root or not candidate.is_dir():
        return None
    return candidate


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_audit(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "audit.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # a malformed line is reported nowhere else either; skip it
    return events


def _step_order(events: list[dict[str, Any]]) -> list[str]:
    """Steps in the order they were first started, from the audit log itself.

    Not read from `src/registry.py` — this service does not import the
    scientific package at all, so step order here is whatever the run's own
    audit log recorded, not a second copy of the registry that could drift
    from it.
    """
    seen: dict[str, None] = {}
    for event in events:
        if event.get("event") == "step_start" and isinstance(event.get("step"), str):
            seen.setdefault(event["step"], None)
    return list(seen)


def _pending_gate(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The most recent `human_gate_open` with no later `human_gate_close`."""
    open_request: dict[str, Any] | None = None
    for event in events:
        if event.get("event") == "human_gate_open":
            open_request = event
        elif event.get("event") == "human_gate_close":
            open_request = None
    if open_request is None:
        return None
    return {k: v for k, v in open_request.items() if k not in {"ts", "event"}}


def _derive_status(events: list[dict[str, Any]], *, has_report: bool) -> str:
    if has_report:
        return "completed"
    if _pending_gate(events) is not None:
        return "halted"
    if events:
        return "running"
    return "unknown"


def _judge_verdicts(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest judge verdict per step, keyed by step name."""
    verdicts: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") == "judge" and isinstance(event.get("step"), str):
            verdicts[event["step"]] = {
                "verdict": event.get("verdict"),
                "score": event.get("score"),
                "reasons": event.get("reasons") or [],
            }
    return verdicts


def _step_status(events: list[dict[str, Any]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        if event.get("event") == "step_end" and isinstance(event.get("step"), str):
            statuses[event["step"]] = str(event.get("status"))
    return statuses


#: Where a report may sit inside a run directory, in the order searched.
#:
#: `build_report/` is where the executor's own step writes it — a real run has
#: `build_report/report.md` and `build_report/report.html`, recorded in that
#: step's `output.json` as `markdown_path` and `html_path`. Those recorded
#: values are *not* used to locate the file: they are paths relative to the
#: project root of the machine that produced the run, and they carry that
#: run's original id, so a run kept by copying it into `results/` under a new
#: name records a path that no longer resolves. What the record does tell us
#: is the layout *within* a run, which is what these constants encode.
#:
#: The run root is searched too, because that is where a simplified or
#: hand-assembled run directory puts it.
#:
#: Both entries are literals joined onto the resolved run directory, so
#: neither can point outside it — the traversal guarantee in
#: `resolve_run_dir` is not weakened by looking in a second place.
REPORT_LOCATIONS = ("build_report", ".")


def _find_report(run_dir: Path, filename: str) -> Path | None:
    """The report file inside `run_dir`, or None if it was never produced."""
    for location in REPORT_LOCATIONS:
        candidate = (run_dir / location / filename) if location != "." else (run_dir / filename)
        if candidate.is_file():
            return candidate
    return None


def _has_report(run_dir: Path) -> bool:
    return (
        _find_report(run_dir, "report.md") is not None
        or _find_report(run_dir, "report.html") is not None
    )


def list_runs(runs_root: Path) -> list[dict[str, Any]]:
    """One summary row per subdirectory of `runs_root` that looks like a run."""
    rows: list[dict[str, Any]] = []
    for child in sorted(runs_root.iterdir()):
        if not child.is_dir() or not RUN_ID_RE.match(child.name):
            continue
        metadata = _read_json(child / "run_metadata.json")
        if metadata is None:
            continue  # not a run directory this service recognises
        events = _read_audit(child)
        has_report = _has_report(child)
        rows.append({
            "scientific_run_id": child.name,
            "status": _derive_status(events, has_report=has_report),
            "started_at": (metadata.get("runtime") or {}).get("started_at"),
            "steps_recorded": len(_step_order(events)),
        })
    return rows


def get_run_snapshot(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    metadata = _read_json(run_dir / "run_metadata.json")
    if metadata is None:
        return None
    events = _read_audit(run_dir)
    has_report = _has_report(run_dir)
    step_order = _step_order(events)
    step_status = _step_status(events)
    verdicts = _judge_verdicts(events)
    return {
        "scientific_run_id": run_id,
        "status": _derive_status(events, has_report=has_report),
        "started_at": (metadata.get("runtime") or {}).get("started_at"),
        "species": (metadata.get("source") or {}).get("config", {}).get("species"),
        "steps": [
            {"step": s, "status": step_status.get(s, "unknown"),
             "verdict": (verdicts.get(s) or {}).get("verdict")}
            for s in step_order
        ],
        "pending_gate": _pending_gate(events),
        "has_report": has_report,
    }


def get_steps(runs_root: Path, run_id: str) -> list[dict[str, Any]] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    events = _read_audit(run_dir)
    step_status = _step_status(events)
    verdicts = _judge_verdicts(events)
    rows = []
    for step in _step_order(events):
        output = _read_json(run_dir / step / "output.json") or {}
        rows.append({
            "step": step,
            "status": step_status.get(step, "unknown"),
            "verdict": verdicts.get(step),
            # A projection, not the raw output — an output.json can be large
            # and step-specific; the gateway promises a bounded shape, so it
            # exposes exactly these three fields and nothing else the step
            # happened to return.
            "output_summary": {
                "warnings": output.get("warnings") or [],
                "errors": output.get("errors") or [],
                "metrics": output.get("metrics") or {},
            },
        })
    return rows


def get_report(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    md_path = _find_report(run_dir, "report.md")
    if md_path is None:
        return {"available": False, "reason": "no report has been produced for this run yet",
                "format": None, "content": None, "source_path": None}
    return {
        "available": True,
        "reason": None,
        "format": "markdown",
        "content": md_path.read_text(encoding="utf-8"),
        # Where it was actually found, relative to the run directory. Two
        # layouts exist in practice, and a reader comparing this projection
        # against a run on disk should not have to guess which one produced it.
        "source_path": md_path.relative_to(run_dir).as_posix(),
    }


#: `run_metadata.json` keys the gateway will project. Everything else in the
#: file — in particular `source.command`, which on a real run can carry a
#: local absolute path — never reaches a response. Adding a key here is a
#: deliberate widening of what a browser can see and should be reviewed as one.
_PROVENANCE_SOURCE_FIELDS = ("commit", "branch", "dirty", "config", "config_sha256", "input_digest")


def get_provenance(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    metadata = _read_json(run_dir / "run_metadata.json")
    if metadata is None:
        return None
    source = metadata.get("source") or {}
    return {
        "scientific_run_id": run_id,
        "source": {field: source.get(field) for field in _PROVENANCE_SOURCE_FIELDS},
        "packages": metadata.get("packages") or {},
        "seeds": metadata.get("seeds") or {},
        "study_design": metadata.get("study_design") or {},
        "judge_sessions": metadata.get("judge_sessions") or [],
        "revisions": metadata.get("revisions") or [],
    }

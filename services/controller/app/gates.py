"""Which gate a run is waiting at, read from the run's own audit log.

Read-only. There is no function in this module that opens a file for writing,
and none that imports the graph — the controller validates a decision, it does
not apply one. Applying is the worker's, through
`src.service.continue_checkpoint_once`.

## Why the audit log and not the checkpoint

The checkpoint is the authority on where the graph stopped, and reading it means
opening a LangGraph SQLite saver, which means importing the scientific package
into the controller. The audit log records the same fact — `human_gate_open`
with no later `human_gate_close` — as plain JSON lines the executor already
writes for exactly this purpose, and it is what the read-only gateway projects
too. Two readers, one recorded fact, no second implementation of the graph.

## Generation, and why a decision needs one

A run can open several gates, and it can open the *same* gate twice: `revise`
routes back to the step, which runs again, and can stop again. So "the pending
gate" is not identified by its name. It is identified by how many gates this run
has opened, which is a number that only goes up and is derivable from the log
alone.

A decision carries the generation it was made against. Answering generation 3
when the run has moved on to 4 is a person acting on a page they loaded before
somebody else answered, and it is refused — the alternative is applying an
answer to a question that was never shown.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

#: Same shape the gateway enforces: no `/`, no `..`, no leading dot. A run id
#: that cannot climb out of the runs root before any path resolution happens.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def resolve_run_dir(runs_root: Path, run_id: str) -> Path | None:
    """The run's directory, or None. Two checks, same reasoning as the gateway.

    The regex stops traversal in the id itself; the containment check after
    `resolve()` stops a symlink inside the root from pointing outside it.
    """
    if not RUN_ID_RE.match(run_id or ""):
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


def read_audit(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "audit.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def gate_id_for(run_id: str, generation: int, gate: str, step: str) -> str:
    """A stable handle for one pending gate of one run.

    Derived rather than stored, so the controller, the worker and the browser
    all compute the same id from the same recorded facts, and none of them has
    to be told it by another.
    """
    seed = f"{run_id}:{generation}:{gate}:{step}".encode("utf-8")
    return "gate_" + hashlib.sha256(seed).hexdigest()[:16]


def gate_state(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    """Everything a decision has to be checked against, or None if no such run.

    `pending` is None when the run is not waiting on anybody, which is a
    different answer from "no such run" and is why this returns a dict with a
    null field rather than None.
    """
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    events = read_audit(run_dir)

    generation = 0
    pending: dict[str, Any] | None = None
    for event in events:
        if event.get("event") == "human_gate_open":
            generation += 1
            pending = event
        elif event.get("event") == "human_gate_close":
            pending = None

    has_report = any(
        (run_dir / location / name).is_file()
        for location in ("build_report", ".")
        for name in ("report.md", "report.html")
    )

    if pending is None:
        status = "completed" if has_report else ("running" if events else "queued")
        return {
            "scientific_run_id": run_id,
            "status": status,
            "generation": generation,
            "gate_id": None,
            "pending_gate": None,
            "has_report": has_report,
        }

    question = {k: v for k, v in pending.items() if k not in {"ts", "event"}}
    return {
        "scientific_run_id": run_id,
        "status": "needs_review",
        "generation": generation,
        "gate_id": gate_id_for(run_id, generation, str(question.get("gate")), str(question.get("step"))),
        "pending_gate": question,
        "has_report": has_report,
    }

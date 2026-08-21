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
import os
import re
import time
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any


class _Unset:
    """"The caller said nothing", where `None` is already an answer.

    Used only by `_derive_status`, whose two optional inputs are both
    legitimately `None` — no gate is open, no step is unfinished — so `None`
    cannot double as "not supplied".
    """


_UNSET = _Unset()

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


#: The step that started and never finished, or None.
#:
#: A run writes `step_start` and then `step_end` for the same step. One without
#: the other means the run was inside that step when the record stops — either
#: because it is still working, or because whatever was working is gone.
def _unfinished_step(events: list[dict[str, Any]]) -> str | None:
    open_step: str | None = None
    for event in events:
        name = event.get("step")
        if not isinstance(name, str):
            continue
        if event.get("event") == "step_start":
            open_step = name
        elif event.get("event") in {"step_end", "step_skipped"} and name == open_step:
            open_step = None
    return open_step


#: How long a run may write nothing before "in progress" stops being the honest
#: reading of it.
#:
#: This is a threshold and therefore a judgement, so it is named, generous, and
#: reported alongside its evidence rather than applied silently — `last_activity_at`
#: travels in the projection so a reader can see what the call was made on.
#:
#: Generous because the executor legitimately goes quiet: `cellranger_count` on
#: a real library runs for tens of minutes, and an audit log only gains an entry
#: when a step begins or ends. Too short a threshold would libel a working run,
#: which is the worse error of the two — a run wrongly called interrupted invites
#: somebody to start a second one on top of it.
#:
#: `GATEWAY_STALE_AFTER_SECONDS` overrides it for a deployment whose steps are
#: slower than this one's.
DEFAULT_STALE_AFTER_SECONDS = 90 * 60


def stale_after_seconds() -> float:
    raw = os.environ.get("GATEWAY_STALE_AFTER_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_STALE_AFTER_SECONDS
    except ValueError:
        return DEFAULT_STALE_AFTER_SECONDS
    return value if value > 0 else DEFAULT_STALE_AFTER_SECONDS


def _last_activity(run_dir: Path) -> float | None:
    """When this run last wrote anything, as a POSIX timestamp.

    `audit.jsonl` is the heartbeat: every step boundary and every verdict
    appends to it, so its mtime is the last moment the executor was
    demonstrably alive. The run directory's own mtime is taken too, because a
    step that creates its output folder touches the parent before it has
    anything to record.

    Two stats, not a walk. A finished run holds gigabytes of `.h5ad` and this
    function runs on every request for every run in the list.
    """
    newest: float | None = None
    for candidate in (run_dir / "audit.jsonl", run_dir):
        try:
            stamp = candidate.stat().st_mtime
        except OSError:
            continue
        newest = stamp if newest is None else max(newest, stamp)
    return newest


def _derive_status(
    events: list[dict[str, Any]],
    *,
    has_report: bool,
    last_activity: float | None = None,
    now: float | None = None,
    pending_gate: dict[str, Any] | None | _Unset = _UNSET,
    unfinished_step: str | None | _Unset = _UNSET,
) -> str:
    """Where this run stands, in the executor's own vocabulary.

    `pending_gate` and `unfinished_step` may be passed in by a caller that has
    already derived them — `RunAudit.status` does — and are derived here when
    they are not. They are sentinel-defaulted rather than `None`-defaulted
    because `None` is a meaningful answer to both questions: no gate is open,
    no step is unfinished. A `None` default would make "the caller knows there
    is no pending gate" indistinguishable from "the caller did not say", and
    this function would go looking for a gate it had just been told is absent.

    The words match `src/state.py::RunStatus` on purpose. This used to return
    `halted` for a run waiting at a gate, which is the executor's word for a
    run a person *stopped* — so the two situations a reader most needs to tell
    apart shared a name, and the app's "needs attention" counter, which looks
    for `needs_review`, could never find one.

    `interrupted` is this projection's own word, and it is the one thing here
    that is inferred rather than recorded. The gateway reads files; it cannot
    see processes, and the run it is describing may not even be on this
    machine. So it says what it can defend: this run stopped mid-step and has
    written nothing since, for longer than any step here takes. A caller that
    wants to judge for itself gets `last_activity_at` in the same response.
    """
    if has_report:
        return "completed"
    gate = _pending_gate(events) if isinstance(pending_gate, _Unset) else pending_gate
    if gate is not None:
        return "needs_review"
    if not events:
        return "unknown"

    unfinished = (
        _unfinished_step(events) if isinstance(unfinished_step, _Unset) else unfinished_step
    )
    if unfinished is not None and last_activity is not None:
        elapsed = (now if now is not None else time.time()) - last_activity
        if elapsed > stale_after_seconds():
            return "interrupted"
    return "running"


def _parse_ts(value: Any) -> float | None:
    """An audit timestamp as a POSIX float, or None if it is not one.

    Every event carries `ts` as ISO-8601 with an offset. A run written by an
    older executor, or a line somebody hand-edited, must not take the timing
    projection down with it — timing is a convenience, and the step record it
    hangs off is not.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _step_durations(events: list[dict[str, Any]]) -> dict[str, float]:
    """How long each finished step took, in seconds, from its own audit pair.

    Measured from `step_start` to `step_end` for the same step. A step with no
    end is absent rather than zero: it is still running, or the run died in it,
    and both of those are questions this dict does not answer.

    Note the executor re-emits `step_start` when a step is retried after a
    `revise`, so the *last* start before the end is the one that produced the
    result on disk. That is the duration a person is asking about.
    """
    starts: dict[str, float] = {}
    out: dict[str, float] = {}
    for event in events:
        name = event.get("step")
        if not isinstance(name, str):
            continue
        stamp = _parse_ts(event.get("ts"))
        if stamp is None:
            continue
        if event.get("event") == "step_start":
            starts[name] = stamp
        elif event.get("event") == "step_end" and name in starts:
            out[name] = max(0.0, stamp - starts.pop(name))
    return out


def _current_step_started(events: list[dict[str, Any]]) -> float | None:
    """When the step a run is currently inside began, or None.

    Paired with `_unfinished_step`: together they are "what is it doing, and
    for how long". Without the second half a run that has been in
    `cellranger_count` for forty minutes and one that entered it twenty seconds
    ago look identical, and the first is normal while the second is the whole
    of what somebody wants to know.
    """
    started: float | None = None
    open_step: str | None = None
    for event in events:
        name = event.get("step")
        if not isinstance(name, str):
            continue
        if event.get("event") == "step_start":
            open_step, started = name, _parse_ts(event.get("ts"))
        elif event.get("event") == "step_end" and name == open_step:
            open_step, started = None, None
    return started if open_step else None


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


#: The run-level headline numbers, and which recorded metric each is read from.
#:
#: Every `(step, key)` pair below was taken from the skill that writes it, not
#: guessed: `run_clustering` puts `n_clusters` in its metrics,
#: `annotate_cells` puts `n_cells` and `n_cell_types`,
#: `apply_cell_qc_filter` puts `n_cells`, and `cross_check_annotation` puts
#: `clusters_scored`. Pairs are tried in order and the first one present wins,
#: which is why the later-running step comes first: a cell count after
#: annotation describes the object the report is about, and a count from
#: `post_load_validate` describes the object before anything was filtered.
#:
#: A number that no step recorded stays `None` and is rendered as "not
#: recorded" rather than reconstructed. That is the rule
#: `docs/report_contract.md` already imposes on the report, and it matters
#: here: a run kept by copying it into `results/` may have had most of its
#: per-step `output.json` files dropped, and inventing a plausible cell count
#: for one of those is exactly the failure the rule exists to prevent.
HEADLINE_METRICS: dict[str, tuple[tuple[str, str], ...]] = {
    "cells": (
        ("annotate_cells", "n_cells"),
        ("apply_cell_qc_filter", "n_cells"),
        ("post_load_validate", "n_obs"),
    ),
    "clusters": (
        ("run_clustering", "n_clusters"),
        ("cross_check_annotation", "clusters_scored"),
    ),
    "cell_types": (
        ("annotate_cells", "n_cell_types"),
    ),
}


def _headline(run_dir: Path) -> dict[str, int | None]:
    """Run-level summary numbers, or None per field where nothing recorded one."""
    cache: dict[str, dict[str, Any]] = {}
    summary: dict[str, int | None] = {}
    for field, sources in HEADLINE_METRICS.items():
        summary[field] = None
        for step, key in sources:
            if step not in cache:
                cache[step] = (_read_json(run_dir / step / "output.json") or {}).get("metrics") or {}
            value = cache[step].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                summary[field] = int(value)
                break
    return summary


def _iso(stamp: float | None) -> str | None:
    """A POSIX timestamp as UTC ISO-8601, or None. Rendered rather than raw so
    a browser is not asked to know which epoch the number came from."""
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(timespec="seconds")


def _has_report(run_dir: Path) -> bool:
    return (
        _find_report(run_dir, "report.md") is not None
        or _find_report(run_dir, "report.html") is not None
    )


class RunAudit:
    """One run's recorded facts, each derived at most once.

    Every projection below asks the same run several overlapping questions, and
    they were each answering from scratch. `get_run_snapshot` called
    `_current_step_started` three times in three adjacent dictionary entries;
    `_derive_status` re-derived the pending gate and the unfinished step that
    its caller then derived again for its own fields. On the run list that is
    per run, for every run, on every request.

    So this holds the run directory and answers each question once. It is
    **not a cache**: an instance is built inside one call, answers that call,
    and is discarded. Two requests share nothing, and the projection is still
    rebuilt from the files every time — which is the property that makes this
    service unable to serve a stale run, and is worth more than the reads it
    would save.

    ## Why the walks are not merged into one loop

    They look mergeable and are not. `unfinished_step` clears its open step on
    `step_end` *or* `step_skipped`; `current_step_started` clears only on
    `step_end`; `step_durations` pops the start it consumed. Folding those into
    a single pass means choosing one of the three terminating rules for all of
    them, which changes what at least two of them report. The duplication this
    removes is *calling* them repeatedly, not the passes themselves.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @cached_property
    def events(self) -> list[dict[str, Any]]:
        return _read_audit(self.run_dir)

    @cached_property
    def step_order(self) -> list[str]:
        return _step_order(self.events)

    @cached_property
    def step_status(self) -> dict[str, str]:
        return _step_status(self.events)

    @cached_property
    def verdicts(self) -> dict[str, dict[str, Any]]:
        return _judge_verdicts(self.events)

    @cached_property
    def durations(self) -> dict[str, float]:
        return _step_durations(self.events)

    @cached_property
    def pending_gate(self) -> dict[str, Any] | None:
        return _pending_gate(self.events)

    @cached_property
    def unfinished_step(self) -> str | None:
        return _unfinished_step(self.events)

    @cached_property
    def current_step_started(self) -> float | None:
        return _current_step_started(self.events)

    @cached_property
    def last_activity(self) -> float | None:
        return _last_activity(self.run_dir)

    @cached_property
    def has_report(self) -> bool:
        return _has_report(self.run_dir)

    def status(self, *, now: float | None = None) -> str:
        """`_derive_status`, given what this object has already worked out.

        Passing the pending gate and the unfinished step in rather than letting
        `_derive_status` find them again is the whole point: they are the two
        it used to recompute behind its caller's back.
        """
        return _derive_status(
            self.events,
            has_report=self.has_report,
            last_activity=self.last_activity,
            now=now,
            pending_gate=self.pending_gate,
            unfinished_step=self.unfinished_step,
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
        audit = RunAudit(child)
        rows.append({
            "scientific_run_id": child.name,
            "status": audit.status(),
            "started_at": (metadata.get("runtime") or {}).get("started_at"),
            # The evidence behind a `running` or `interrupted` verdict, so the
            # threshold that produced it can be second-guessed by whoever is
            # looking at the row rather than only trusted.
            "last_activity_at": _iso(audit.last_activity),
            # Which step a run is sitting inside, in the *list* and not only in
            # the detail. It is what makes a row actionable: "waiting" tells
            # somebody to open it, "waiting at apply_cell_qc_filter" tells them
            # what they are about to be asked. Already computed for the detail
            # from the same events, so this costs a function call, not a read.
            "unfinished_step": audit.unfinished_step,
            # Which step the run is *asking about*, which is not the same thing
            # as the step it is inside. A gate opens after its step has already
            # finished — `apply_cell_qc_filter` writes `step_end` and only then
            # asks whether to apply the cut — so `unfinished_step` is null for
            # exactly the runs a person most needs to identify. Only the name
            # travels; the evidence stays in the detail.
            "pending_gate_step": (audit.pending_gate or {}).get("step"),
            "steps_recorded": len(audit.step_order),
            **_headline(child),
        })
    return rows


def get_run_snapshot(runs_root: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    metadata = _read_json(run_dir / "run_metadata.json")
    if metadata is None:
        return None
    audit = RunAudit(run_dir)
    verdicts = audit.verdicts
    # Derived once and read three times below. It used to be called three times
    # in these three adjacent entries, which is the clearest instance of what
    # `RunAudit` exists to stop.
    started = audit.current_step_started
    return {
        "scientific_run_id": run_id,
        "status": audit.status(),
        "last_activity_at": _iso(audit.last_activity),
        # Named so a reader of an `interrupted` run knows where it stopped, and
        # so `--resume-from` has somewhere to start from.
        "unfinished_step": audit.unfinished_step,
        # How long it has been in that step. "In cellranger_count" and "in
        # cellranger_count for 40 minutes" are different facts, and only the
        # second one lets somebody tell a working run from a stuck one — which
        # is the question every person watching a long step actually has.
        "current_step_started_at": _iso(started),
        "current_step_elapsed_seconds": (
            None if started is None else round(time.time() - started, 1)
        ),
        "started_at": (metadata.get("runtime") or {}).get("started_at"),
        "species": (metadata.get("source") or {}).get("config", {}).get("species"),
        "steps": [
            {"step": s, "status": audit.step_status.get(s, "unknown"),
             "verdict": (verdicts.get(s) or {}).get("verdict")}
            for s in audit.step_order
        ],
        "pending_gate": audit.pending_gate,
        "has_report": audit.has_report,
        # Counted from the audit log, so a run whose per-step output.json
        # files were not kept still reports its verdict tally correctly.
        "warn_count": sum(1 for v in verdicts.values() if v.get("verdict") == "warn"),
        "fail_count": sum(1 for v in verdicts.values() if v.get("verdict") == "fail"),
        "reused_steps": sorted(
            {str(e.get("step")) for e in audit.events if e.get("event") == "step_skipped"}
        ),
        **_headline(run_dir),
    }


#: How many per-file FastQC rows a step detail may carry before it is cut.
#: A 10x run is three reads per lane per sample, so this is generous for a
#: handful of libraries and bounded for a large one. A cut is declared in the
#: response rather than silently applied — the same rule `src/nodes.py` uses
#: when it abridges a step's output for the judge.
MAX_FASTQC_FILES = 24


def _project_fastq_qc(output: dict[str, Any]) -> dict[str, Any]:
    """`fastq_qc`'s own numbers, as a browser may see them.

    `report_dir` and `multiqc_report` are absolute paths on the machine that
    ran the analysis and are deliberately not projected — only whether a
    MultiQC report exists. Serving the file itself needs an artifact endpoint
    that does not exist yet; announcing its filesystem location in the
    meantime would put a host path in a browser response for no gain.

    `module_status` is dropped from each file: it repeats every FastQC module
    name for every file, and `modules_failed` / `modules_warned` already carry
    the part a reader acts on.
    """
    reports = output.get("reports")
    files: list[dict[str, Any]] = []
    if isinstance(reports, list):
        for report in reports[:MAX_FASTQC_FILES]:
            if not isinstance(report, dict):
                continue
            files.append({
                key: report.get(key)
                for key in (
                    "file", "read_role", "total_sequences", "sequence_length",
                    "pct_gc", "q30_fraction", "duplicate_fraction",
                    "max_adapter_pct", "modules_failed", "modules_warned",
                )
            })
    total_files = len(reports) if isinstance(reports, list) else 0
    return {
        "per_read_role": output.get("per_read_role") or {},
        "files": files,
        "files_total": total_files,
        "files_shown": len(files),
        "module_failures": output.get("module_failures") or {},
        "expected_module_flags": output.get("expected_module_flags") or [],
        "notes": output.get("notes") or [],
        "has_multiqc_report": bool(output.get("multiqc_report")),
    }


def _project_cellranger(output: dict[str, Any]) -> dict[str, Any]:
    """`cellranger_count`'s per-library top-line numbers.

    `metrics_summary` is Cell Ranger's own `metrics_summary.csv`, already
    parsed by the skill, so this passes it through as recorded rather than
    renaming or reformatting anything — the column names in a Cell Ranger
    report are what a person will be comparing against.

    Every path the step records — `outs`, `bam`, the two matrices and
    `web_summary` — is an absolute local path and is dropped. Only whether a
    web summary exists is reported.
    """
    libraries = output.get("libraries")
    rows: list[dict[str, Any]] = []
    if isinstance(libraries, list):
        for library in libraries:
            if not isinstance(library, dict):
                continue
            rows.append({
                "library_id": library.get("library_id"),
                "chemistry": library.get("chemistry"),
                "metrics_summary": library.get("metrics_summary") or {},
                "has_web_summary": bool(library.get("web_summary")),
            })
    return {"libraries": rows}


#: Steps whose own output carries numbers worth showing beside the judge's
#: verdict, and the projection that decides what a browser sees of each. A
#: named projection of known structure, not a byte cap: a cap would cut
#: wherever the budget ran out, which could be the very field somebody is
#: looking at, and nothing would say so.
UPSTREAM_DETAIL: dict[str, Any] = {
    "fastq_qc": _project_fastq_qc,
    "cellranger_count": _project_cellranger,
}


#: Figure filenames are named for the report section they illustrate — `m3_`,
#: `a4_` — rather than for the step that produced the numbers behind them, so
#: attributing one to a step needs a table. It lives here because it is a fact
#: about `build_report`'s naming and nothing else knows it.
#:
#: Read the other way round on purpose: a step may own several figures, and
#: several steps may legitimately own none. A prefix absent from this table is
#: simply not shown against any step, rather than guessed at from its name.
FIGURES_BY_STEP: dict[str, tuple[str, ...]] = {
    "run_qc_metrics": ("m2_qc", "a2_qc_per_sample"),
    "apply_cell_qc_filter": ("m1_funnel", "a3_filter_reasons"),
    "detect_doublets": ("a4_doublets",),
    "normalize_hvg_prepare": ("a5_pca_hvg",),
    "run_pca": ("a5_pca_hvg",),
    "run_umap": ("m3_umap", "m3_umap_3d", "m3_tsne", "m3_tsne_3d"),
    "find_markers": ("m4_markers",),
    "annotate_cells": ("m6_confidence",),
    "cross_check_annotation": ("m7_cross_check",),
}

#: Keys every step's `output.json` carries that are not the step's own
#: settings: bookkeeping, routing hints, and paths.
#:
#: Paths are excluded because they are absolute on the machine that ran the
#: analysis, and this service must not repeat one into a browser response —
#: the same rule `get_provenance` follows for `source.command`.
_NOT_SETTINGS = frozenset({
    "metrics", "warnings", "errors", "notes", "recommended_next_tool",
    "adata_path", "adata_paths", "marker_table_path", "cell_flags_path",
    "markdown_path", "html_path", "model_path", "figure_paths",
    "embedding_data_paths", "matrix_path", "matrix_paths", "report_dir",
    "multiqc_report", "outs", "bam", "web_summary", "reports", "libraries",
})

#: How many entries of a nested settings block are projected. A `per_cluster`
#: table on a 15-cluster run is large and belongs on the step's own page, not
#: in a timeline row; the projection says what it cut rather than truncating in
#: silence.
MAX_SETTING_ENTRIES = 12


def _project_settings(output: dict[str, Any]) -> dict[str, Any]:
    """The values that describe *how this step ran*, as a browser may see them.

    This is the third tier `docs/report_contract.md` calls the reason the
    pipeline exists — "who decided what, and can it be rerun" — and every field
    of it was already being written to disk. It simply was not being projected,
    so the app could show that a step passed and not what it passed *with*.
    """
    settings: dict[str, Any] = {}
    for key, value in output.items():
        if key in _NOT_SETTINGS or key.endswith("_path") or key.endswith("_paths"):
            continue
        if isinstance(value, dict):
            items = list(value.items())
            projected = {k: v for k, v in items[:MAX_SETTING_ENTRIES]}
            if len(items) > MAX_SETTING_ENTRIES:
                projected["…"] = f"{len(items) - MAX_SETTING_ENTRIES} more not shown"
            settings[key] = projected
        elif isinstance(value, list):
            settings[key] = value[:MAX_SETTING_ENTRIES]
        else:
            settings[key] = value
    return settings


def _step_figures(run_dir: Path, step: str) -> list[dict[str, str]]:
    """The figures this step produced, as artifact ids the browser may fetch.

    Ids come from `artifacts.list_artifacts`, which is the whole access-control
    surface for run files — an id that does not appear there cannot be fetched.
    Building them any other way here would be a second way to name a file.
    """
    prefixes = FIGURES_BY_STEP.get(step)
    if not prefixes:
        return []
    from . import artifacts as artifact_store

    found: list[dict[str, str]] = []
    for entry in artifact_store.list_artifacts(run_dir):
        if entry["kind"] != "figure":
            continue
        stem = Path(entry["name"]).stem
        if stem in prefixes:
            found.append({
                "artifact_id": entry["artifact_id"],
                "name": entry["name"],
                "label": entry["label"],
            })
    return found


def get_steps(runs_root: Path, run_id: str) -> list[dict[str, Any]] | None:
    run_dir = resolve_run_dir(runs_root, run_id)
    if run_dir is None:
        return None
    audit = RunAudit(run_dir)
    step_status = audit.step_status
    verdicts = audit.verdicts
    durations = audit.durations
    rows = []
    for step in audit.step_order:
        output = _read_json(run_dir / step / "output.json") or {}
        rows.append({
            "step": step,
            "status": step_status.get(step, "unknown"),
            "verdict": verdicts.get(step),
            # A projection, not the raw output — an output.json can be large
            # and step-specific; the gateway promises a bounded shape, so it
            # exposes exactly these fields and nothing else the step happened
            # to return.
            "output_summary": {
                "warnings": output.get("warnings") or [],
                "errors": output.get("errors") or [],
                "metrics": output.get("metrics") or {},
            },
            # What the step said about its own result, in its own words. These
            # were recorded from the beginning and shown nowhere: `run_clustering`
            # writes "the smallest cluster has only 8 cells; may be noise rather
            # than a population" while its judge returns `pass`, because the
            # judge is asked whether the step ran soundly and by that measure it
            # did. A scientific reservation that reaches disk and not the screen
            # is a reservation nobody acts on.
            "notes": output.get("notes") or [],
            # How long it took, from its own audit pair. Absent rather than
            # zero for a step with no recorded end — still running, or the run
            # died inside it, and neither is "took no time".
            "duration_seconds": durations.get(step),
            # How this step ran: the settings, thresholds and choices behind the
            # numbers. `docs/report_contract.md` calls this the tier that is the
            # reason the pipeline exists, and it was already on disk.
            "settings": _project_settings(output),
            # The figures this step's numbers produced, by artifact id. They all
            # live in `build_report/figures/` under report-section names, so
            # without this a person looking at `detect_doublets` has to know
            # that its plot is called `a4_doublets`.
            "figures": _step_figures(run_dir, step),
            # Present only for the upstream steps that carry their own QC
            # numbers (FastQC per file, Cell Ranger per library). Absent —
            # not empty — for every other step, so a reader can tell "this
            # step has no such detail" from "this step recorded none".
            **(
                {"upstream_detail": UPSTREAM_DETAIL[step](output)}
                if step in UPSTREAM_DETAIL and output
                else {}
            ),
        })
    return rows


#: How many finished runs a per-step timing is drawn from before it is offered
#: as an expectation. One run is an anecdote — the same step on the same data
#: varies by minutes between runs on a loaded machine — and a single sample
#: presented as "usually takes" is a claim this projection has not earned.
MIN_RUNS_FOR_TIMING = 2


def step_timings(runs_root: Path) -> dict[str, Any]:
    """What each step has actually taken on this machine, from finished runs.

    Every number a person is shown about how long something will take comes
    from here, and therefore from measurement rather than from a sentence
    somebody wrote once. That matters more than it sounds: `cellranger_count`
    on a 1k-cell library and on a 10k-cell one differ by a factor that no
    hardcoded estimate survives, and the machine's own history is the only
    thing that knows which one this is.

    Only completed runs contribute. A run that was interrupted inside a step
    has no end for it, and a run that was stopped at a gate spent an unbounded
    amount of that step's wall-clock waiting for a person — counting either as
    "how long the step takes" would make every future estimate wrong in the
    same direction.

    Reported with `n` and the full range, not a lone median. A reader deciding
    whether to wait or investigate needs to know whether "about 30 minutes" was
    drawn from two runs that took 12 and 48.
    """
    per_step: dict[str, list[float]] = {}
    contributing = 0
    for child in sorted(runs_root.iterdir()) if runs_root.is_dir() else []:
        if not child.is_dir():
            continue
        audit = RunAudit(child)
        if not audit.events:
            continue
        if audit.status() != "completed":
            continue
        contributing += 1
        for step, seconds in audit.durations.items():
            per_step.setdefault(step, []).append(seconds)

    steps: dict[str, Any] = {}
    for step, samples in per_step.items():
        if len(samples) < MIN_RUNS_FOR_TIMING:
            continue
        ordered = sorted(samples)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        steps[step] = {
            "n": len(ordered),
            "median_seconds": round(median, 1),
            "min_seconds": round(ordered[0], 1),
            "max_seconds": round(ordered[-1], 1),
        }
    return {
        "steps": steps,
        "runs_measured": contributing,
        "min_runs_required": MIN_RUNS_FOR_TIMING,
        # Named so a caller can say "no estimate yet" rather than rendering an
        # empty object as "instant".
        "total_median_seconds": (
            round(sum(v["median_seconds"] for v in steps.values()), 1) if steps else None
        ),
    }


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

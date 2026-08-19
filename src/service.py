"""The seam a front end that is not a terminal reaches the executor through.

Two functions, both very thin, both deliberately additive: nothing here changes
what `run_workflow` or `continue_workflow` already do, and every existing caller
of either keeps its behaviour byte for byte. What they add is the shape a
non-interactive caller needs, which the terminal never did:

`start_detached_run` starts a run that is *allowed to stop* and has nobody
standing by to answer it. That combination already worked — a durable
checkpointer plus `decide=None` suspends at the first gate and returns — but a
caller had to know that `decide=None` means "leave it waiting" rather than "wave
it through", and had to discover the run id from the returned state, which is
too late for a worker that wants to record what it is about to start.

`continue_checkpoint_once` answers exactly one gate. `continue_workflow` answers
every gate it reaches, because at a terminal there is a person still sitting
there. A web decision is one HTTP request from one operator about one pending
question: applying it to the *next* gate as well would be answering a question
nobody was shown. So the answer is one-shot, and a run that stops again stops
with a fresh pending question of its own.

Neither function knows anything scientific. Routing stays in `graph.py`,
step order and revisable parameters stay in `registry.py`, and the decision
semantics stay in `nodes.make_human_gate_node`. This module only decides *who
gets asked*, and its answer in both cases is "not this process".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .policy import GatePolicy
from .run import continue_workflow, run_workflow


def allocate_run_id() -> str:
    """A run id in the executor's own format, minted before the run starts.

    `new_run_state` mints one of these when it is not given one. A worker needs
    it earlier than that: it has to record which scientific run a job became
    *before* handing control to the graph, or a crash between the two leaves a
    run directory on disk that no job claims and a job that will start a second
    one when it is retried.
    """
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def start_detached_run(
    *,
    run_id: str,
    project: str,
    config: dict[str, Any],
    input_bundle: dict[str, Any],
    study_design: dict[str, Any] | None = None,
    runs_dir: str = "runs",
    judge_backend: str | None = None,
    judge_model: str | None = None,
    allow_warn: bool = False,
) -> dict[str, Any]:
    """Run until the graph finishes or asks something, then return either way.

    `decide=None` with `interactive=True` is the combination that means "this
    run may stop, and nobody here can answer it". The gate suspends, the
    question is written into `pending_review` by the node before it, and the
    sqlite checkpoint in the run directory keeps all of that answerable from
    another process — which is what `continue_checkpoint_once` is for.

    `headless_decision` is deliberately not set: it only applies when the policy
    is *not* interactive, and a run that silently accepted its own gates is the
    one outcome a web product must never produce.
    """
    return run_workflow(
        project=project,
        config=dict(config),
        input_bundle=dict(input_bundle),
        study_design=dict(study_design or {}),
        run_id=run_id,
        runs_dir=runs_dir,
        checkpointer_kind="sqlite",
        decide=None,
        policy=GatePolicy(autocontinue_on_warn=allow_warn, interactive=True),
        judge_backend=judge_backend,
        judge_model=judge_model,
    )


class _AnswerOnce:
    """Answer the first question and refuse the second.

    `EOFError` is not an error signal invented here — `_answer_until_done`
    already treats it as "there turns out to be nobody to ask", and leaves the
    run suspended with its checkpoint intact. That is precisely the outcome
    wanted after one answer has been applied, so this reuses the existing
    meaning rather than adding a second way to say the same thing.
    """

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.used = False
        self.next_question: dict[str, Any] | None = None

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.used:
            # Recorded before refusing, so the caller can report what the run is
            # now waiting on without reopening the checkpoint to find out.
            self.next_question = request
            raise EOFError("one decision per continuation; this run is waiting again")
        self.used = True
        return self.answer


def continue_checkpoint_once(
    *,
    run_id: str,
    decision: dict[str, Any],
    runs_dir: str = "runs",
    judge_backend: str | None = None,
    judge_model: str | None = None,
    allow_warn: bool = False,
) -> dict[str, Any]:
    """Apply one gate answer to a suspended run and stop asking.

    Returns the final state, with `stopped_again` set when the run reached
    another gate rather than finishing. The caller needs that distinction and
    cannot get it from `status` alone: a run that suspended again and a run that
    was already suspended both read `needs_review`.

    Everything about *how* the answer is applied — the allowlist, the type
    conversion, the revision record, the invalidation of later steps — is
    `nodes.make_human_gate_node` and `registry.coerce_overrides`, unchanged.
    This function chooses who is asked, not what the answer means.
    """
    answerer = _AnswerOnce(dict(decision))
    final = continue_workflow(
        run_id=run_id,
        runs_dir=runs_dir,
        policy=GatePolicy(autocontinue_on_warn=allow_warn, interactive=True),
        judge_backend=judge_backend,
        judge_model=judge_model,
        decide=answerer,
    )
    return {
        **final,
        "stopped_again": answerer.next_question is not None,
        "next_question": answerer.next_question,
    }

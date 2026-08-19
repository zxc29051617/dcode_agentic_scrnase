"""The two additions to the executor's public surface, checked on their own.

`test_web_intake_flow.py` drives these against the real graph, which is the
test that matters. These are the cheap ones that need no environment: that the
one-shot answerer really is one-shot, and that the two run-id arguments cannot
be confused for each other.

Run with `python tests/test_service_seam.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.run import run_workflow  # noqa: E402
from src.service import _AnswerOnce, allocate_run_id  # noqa: E402


def test_the_answerer_answers_once_and_then_refuses():
    answerer = _AnswerOnce({"decision": "accept"})
    assert answerer({"gate": "human_gate", "step": "run_pca"}) == {"decision": "accept"}
    try:
        answerer({"gate": "human_gate", "step": "annotate_cells"})
    except EOFError:
        pass
    else:
        raise AssertionError("a second question must not be answered with the first answer")
    # The refused question is kept, so the caller can report what the run is now
    # waiting on without reopening the checkpoint.
    assert answerer.next_question["step"] == "annotate_cells"


def test_eoferror_is_what_the_executor_already_treats_as_nobody_to_ask():
    """The one-shot relies on an existing meaning rather than inventing one."""
    source = (Path(__file__).resolve().parent.parent / "src" / "run.py").read_text(encoding="utf-8")
    assert "except EOFError" in source, (
        "_AnswerOnce depends on _answer_until_done catching EOFError and leaving the run "
        "suspended; if that handler goes, the seam silently changes meaning"
    )


def test_an_allocated_run_id_has_the_executor_s_own_shape():
    """A worker-minted id and an executor-minted id must be indistinguishable."""
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", allocate_run_id())


def test_naming_a_fresh_run_and_resuming_one_are_different_requests():
    try:
        run_workflow(run_id="a", resume_run_id="b")
    except ValueError as exc:
        assert "not both" in str(exc)
    else:
        raise AssertionError("passing both run ids is a caller contradicting itself")


TESTS = (
    test_the_answerer_answers_once_and_then_refuses,
    test_eoferror_is_what_the_executor_already_treats_as_nobody_to_ask,
    test_an_allocated_run_id_has_the_executor_s_own_shape,
    test_naming_a_fresh_run_and_resuming_one_are_different_requests,
)


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
        except AssertionError as exc:
            failed = 1
            print(f"  FAIL  {test.__name__}: {exc}")
        else:
            print(f"  ok    {test.__name__}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

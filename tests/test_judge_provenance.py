"""Which judge produced which verdict, recorded so it can be checked later.

A verdict is only as interpretable as the thing that produced it. The project's
own model comparison has `gpt-oss:120b` at 12/12 and `llama3.1:8b` at 6/12 on
the same cases, and `medgemma:27b` passing a 6x doublet rate — so two runs of
the same data judged by different models are two different results, and until
now the recorded provenance could not tell them apart.

What is pinned here:

  - the recorded values are the ones that *won*, not the environment's offer
  - prompt hashes are taken over the text the judge read, so an edit moves them
  - a resumed run appends, so a run judged by two models says so
  - no key, token or embedded credential reaches the file

Run with `python tests/test_judge_provenance.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import judge as judge_module  # noqa: E402
from src import run as run_module  # noqa: E402
from src.judge import (  # noqa: E402
    HASH_ALGORITHM,
    LocalLLMJudge,
    StubJudge,
    describe_judge,
    judge_session_id,
    session_fingerprint,
    text_digest,
)
from src.policy import GatePolicy  # noqa: E402
from src.provenance import AuditLog, record_judge_session  # noqa: E402
from src.run import run_workflow  # noqa: E402
from tests import fixtures  # noqa: E402

STEPS = ("run_qc_metrics", "run_pca", "cross_check_annotation")

#: A value that must never appear in a file written beside shared results.
SECRET = "sk-do-not-write-this-anywhere-1234567890"


# --- helpers ----------------------------------------------------------------------


@contextmanager
def prompts(base: str, addenda: dict[str, str] | None = None):
    """Point the judge at prompt files this test controls.

    Patched at the module level because that is where the judge reads them from,
    so the hash under test is taken over exactly the bytes it would have used.
    """
    original_base = judge_module.PROMPT_PATH
    original_dir = judge_module.STEP_PROMPT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base_path = root / "local_judge_base.md"
        base_path.write_text(base, encoding="utf-8")
        step_dir = root / "steps"
        step_dir.mkdir()
        for step, text in (addenda or {}).items():
            (step_dir / f"{step}.md").write_text(text, encoding="utf-8")
        judge_module.PROMPT_PATH = base_path
        judge_module.STEP_PROMPT_DIR = step_dir
        try:
            yield
        finally:
            judge_module.PROMPT_PATH = original_base
            judge_module.STEP_PROMPT_DIR = original_dir


class ModelNamingJudge(StubJudge):
    """A stub that reports a model, so the recording path can be driven offline.

    Subclasses `StubJudge` so its verdicts stay deterministic: this is a test
    about provenance, and a test that needed a live endpoint to check what got
    written down would not be one.
    """

    def __init__(self, model: str, *, per_step: dict[str, str] | None = None) -> None:
        self.model = model
        self.per_step = dict(per_step or {})

    def model_for(self, step: str) -> str:
        return self.per_step.get(step, self.model)

    def describe(self, steps) -> dict:
        return {
            "backend": "local",
            "default_model": self.model,
            "step_models": {step: self.model_for(step) for step in steps},
            "base_prompt_sha256": text_digest("pretend base prompt"),
            "step_prompts": {},
            "temperature": 0.0,
        }


@contextmanager
def judged_by(client):
    """Make `run_workflow` use this judge, without needing an endpoint."""
    original = run_module.get_judge
    run_module.get_judge = lambda _backend=None: client
    try:
        yield
    finally:
        run_module.get_judge = original


def _run(root: Path, **kwargs):
    matrix = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"},
                                 root / "bundle")
    reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
    return run_workflow(
        project="provenance",
        input_bundle={"paths": [str(matrix)]},
        config={"species": "human", "transcriptome": str(reference),
                "min_genes": 1, "max_pct_mito": 100},
        runs_dir=str(root / "runs"),
        policy=GatePolicy(headless_decision="accept"),
        **kwargs,
    )


def _sessions(final) -> list[dict]:
    return json.loads(Path(final["run_metadata_path"]).read_text(encoding="utf-8"))["judge_sessions"]


# --- 1. the stub is recorded, explicitly ---------------------------------------------


def test_a_stub_run_records_its_backend_rather_than_recording_nothing():
    """"the stub scored this" and "nobody knows" must not look the same."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        sessions = _sessions(final)

    assert len(sessions) == 1
    session = sessions[0]
    assert session["backend"] == "stub"
    assert session["mode"] == "new"
    assert session["hash_algorithm"] == HASH_ALGORITHM
    assert session["recorded_at"]
    assert session["default_model"] is None
    assert session["base_prompt_sha256"] is None, "the stub reads no prompt"
    assert "no model was called" in session["note"].lower()
    assert set(session["step_models"]) >= {"run_qc_metrics", "run_pca", "annotate_cells"}


def test_every_judged_step_appears_in_the_record():
    """A step missing from the map is a verdict with no attributable model."""
    from src.registry import REGISTRY

    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        recorded = set(_sessions(final)[0]["step_models"])

    expected = {name for name, spec in REGISTRY.items() if spec.judge}
    assert recorded == expected
    assert "human_review_decision" not in recorded, "a gate is not a judged step"


# --- 2 and 3. the model that won, including per-step ---------------------------------


def test_a_model_backed_judge_records_the_model_it_resolved_to():
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(ModelNamingJudge("gpt-oss:120b")):
            final = _run(Path(tmp))
        session = _sessions(final)[0]

    assert session["backend"] == "local"
    assert session["default_model"] == "gpt-oss:120b"
    assert session["step_models"]["run_pca"] == "gpt-oss:120b"


def test_a_per_step_override_is_recorded_against_that_step_only():
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(ModelNamingJudge("gpt-oss:120b",
                                        per_step={"run_qc_metrics": "small:1b"})):
            final = _run(Path(tmp))
        session = _sessions(final)[0]

    assert session["step_models"]["run_qc_metrics"] == "small:1b"
    assert session["step_models"]["run_pca"] == "gpt-oss:120b"
    assert session["default_model"] == "gpt-oss:120b", "the default is still the default"


def test_the_resolved_value_is_recorded_not_the_environment_variable(monkey_env=None):
    """`--judge` beats the env var, a constructor argument beats `SCRNA_JUDGE_MODEL`.

    So the environment says what was offered and only the live object knows what
    was used. Recording the former would be wrong exactly when it matters.
    """
    import os

    original = os.environ.get("SCRNA_JUDGE_MODEL")
    os.environ["SCRNA_JUDGE_MODEL"] = "env-said-this-one"
    try:
        client = LocalLLMJudge(model="argument-won", api_key="x")
        described = describe_judge(client, STEPS)
    finally:
        if original is None:
            os.environ.pop("SCRNA_JUDGE_MODEL", None)
        else:
            os.environ["SCRNA_JUDGE_MODEL"] = original

    assert described["default_model"] == "argument-won"
    assert "env-said-this-one" not in json.dumps(described)


def test_each_verdict_carries_the_model_that_produced_it():
    """The per-verdict answer, so it is a lookup rather than a join on timestamps."""
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(ModelNamingJudge("gpt-oss:120b",
                                        per_step={"run_qc_metrics": "small:1b"})):
            final = _run(Path(tmp))
        events = [e for e in AuditLog(final["audit_log_path"]).read()
                  if e["event"] == "judge"]

    by_step = {e["step"]: e["model"] for e in events}
    assert by_step["run_qc_metrics"] == "small:1b"
    assert by_step["run_pca"] == "gpt-oss:120b"


def test_a_stub_verdict_says_no_model_rather_than_omitting_the_field():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        events = [e for e in AuditLog(final["audit_log_path"]).read()
                  if e["event"] == "judge"]

    assert events, "nothing was judged"
    for event in events:
        assert "model" in event and event["model"] is None


# --- 4 and 5. prompt hashes track the text --------------------------------------------


def test_the_same_prompt_hashes_the_same_every_time():
    with prompts("the base prompt", {"run_qc_metrics": "the addendum"}):
        first = describe_judge(LocalLLMJudge(api_key="x"), STEPS)
        second = describe_judge(LocalLLMJudge(api_key="x"), STEPS)

    assert first["base_prompt_sha256"] == second["base_prompt_sha256"]
    assert first["step_prompts"] == second["step_prompts"]
    assert len(first["base_prompt_sha256"]) == 64, "sha256, as declared"


def test_changing_one_character_of_the_base_prompt_changes_every_step_hash():
    with prompts("the base prompt", {"run_qc_metrics": "the addendum"}):
        before = describe_judge(LocalLLMJudge(api_key="x"), STEPS)
    with prompts("the base prompt.", {"run_qc_metrics": "the addendum"}):
        after = describe_judge(LocalLLMJudge(api_key="x"), STEPS)

    assert before["base_prompt_sha256"] != after["base_prompt_sha256"]
    for step in STEPS:
        assert before["step_prompts"][step]["prompt_sha256"] \
            != after["step_prompts"][step]["prompt_sha256"], \
            f"{step} is composed from the base and has to move with it"
    assert before["step_prompts"]["run_qc_metrics"]["addendum_sha256"] \
        == after["step_prompts"]["run_qc_metrics"]["addendum_sha256"], \
        "its own file did not change, and the record has to separate the two"


def test_changing_one_character_of_a_step_prompt_moves_only_that_step():
    with prompts("base", {"run_qc_metrics": "look at the mitochondrial fraction"}):
        before = describe_judge(LocalLLMJudge(api_key="x"), STEPS)
    with prompts("base", {"run_qc_metrics": "look at the mitochondrial fractions"}):
        after = describe_judge(LocalLLMJudge(api_key="x"), STEPS)

    assert before["base_prompt_sha256"] == after["base_prompt_sha256"]
    changed = before["step_prompts"]["run_qc_metrics"]
    unchanged = before["step_prompts"]["run_pca"]
    assert changed["prompt_sha256"] != after["step_prompts"]["run_qc_metrics"]["prompt_sha256"]
    assert changed["addendum_sha256"] != after["step_prompts"]["run_qc_metrics"]["addendum_sha256"]
    assert unchanged["prompt_sha256"] == after["step_prompts"]["run_pca"]["prompt_sha256"]


def test_the_hash_is_of_the_composed_prompt_the_model_is_sent():
    """Not of the file, and not of its name: the system message is what judged."""
    with prompts("BASE", {"run_qc_metrics": "EXTRA"}):
        client = LocalLLMJudge(api_key="x")
        described = describe_judge(client, STEPS)
        composed = client.system_prompt_for("run_qc_metrics")

    assert described["step_prompts"]["run_qc_metrics"]["prompt_sha256"] == text_digest(composed)
    assert described["step_prompts"]["run_pca"]["prompt_sha256"] == text_digest("BASE"), \
        "a step with no addendum is judged on the base prompt alone"
    assert described["step_prompts"]["run_pca"]["addendum"] is None


# --- 6. a model change is visible ------------------------------------------------------


def test_running_the_same_run_under_a_different_model_is_visible_in_the_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with judged_by(ModelNamingJudge("first-model")):
            first = _run(root)
        with judged_by(ModelNamingJudge("second-model")):
            _run(root, resume_run_id=first["run_id"])
        sessions = _sessions(first)

    assert [s["default_model"] for s in sessions] == ["first-model", "second-model"]
    assert [s["mode"] for s in sessions] == ["new", "artifact_resume"]


# --- 7. resuming appends -----------------------------------------------------------------


def test_a_resume_appends_and_does_not_overwrite_what_judged_the_first_pass():
    """Otherwise the file claims one model produced verdicts two models produced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root)
        original = _sessions(first)[0]

        _run(root, resume_run_id=first["run_id"])
        after = _sessions(first)

    assert len(after) == 2
    assert after[0] == original, "the first session has to survive byte for byte"
    assert after[1]["mode"] == "artifact_resume"


def test_the_rest_of_the_run_metadata_is_left_alone():
    """Provenance is appended to; the commit and versions still describe run start."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root)
        path = Path(first["run_metadata_path"])
        before = json.loads(path.read_text(encoding="utf-8"))
        _run(root, resume_run_id=first["run_id"])
        after = json.loads(path.read_text(encoding="utf-8"))

    for key in ("run_id", "runtime", "packages", "seeds"):
        assert after[key] == before[key], f"{key} must not move on a resume"
    assert after["source"] == before["source"]


def test_continuing_from_a_checkpoint_records_its_own_judge_too():
    """It builds one, and every step after the gate is scored by it.

    Checked rather than assumed: `continue_workflow` calls `get_judge` and hands
    the result to `build_graph`, so a run picked up after `SCRNA_JUDGE_MODEL`
    changed contributes verdicts from a second model. An entry is appended even
    when the answer turns out to be `stop` and nothing is scored — it states
    which judge was live, and the `judge` events say what it actually scored.
    """
    from src.run import continue_workflow

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with judged_by(ModelNamingJudge("model-that-started-it")):
            paused = run_workflow(
                project="provenance",
                input_bundle={"paths": [str(fixtures.bundle_for(
                    {"input_type": "matrix", "matrix_kind": "filtered"}, root / "bundle"))]},
                config={"species": "human",
                        "transcriptome": str(fixtures.make_reference(
                            root, "ref", genomes=["GRCh38"]))},
                runs_dir=str(root / "runs"),
                policy=GatePolicy(interactive=True),
                checkpointer_kind="sqlite",
                decide=None,
            )
        assert paused["status"] == "needs_review", "precondition: it stopped at a gate"

        with judged_by(ModelNamingJudge("model-that-finished-it")):
            continue_workflow(
                run_id=paused["run_id"], runs_dir=str(root / "runs"),
                policy=GatePolicy(interactive=True),
                decide=lambda _request: {"decision": "stop", "rationale": "enough"},
            )
        sessions = _sessions(paused)

    assert [s["mode"] for s in sessions] == ["new", "checkpoint_continue"]
    assert [s["default_model"] for s in sessions] == [
        "model-that-started-it", "model-that-finished-it",
    ]


def test_a_missing_metadata_file_does_not_stop_the_run():
    assert record_judge_session("/nope/not/here.json", mode="new", session={}) is None


# --- session ids: joining a verdict to the judge that produced it ---------------------


def _judge_events(final) -> list[dict]:
    return [e for e in AuditLog(final["audit_log_path"]).read() if e["event"] == "judge"]


def test_every_verdict_in_one_session_cites_the_same_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        events = _judge_events(final)
        sessions = _sessions(final)

    cited = {e["judge_session_id"] for e in events}
    assert len(events) > 1, "more than one step was judged"
    assert cited == {sessions[0]["session_id"]}, "one session, one id, on every verdict"


def test_a_verdicts_session_id_resolves_in_the_run_metadata():
    """The join has to actually work, not merely look plausible."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        events = _judge_events(final)
        sessions = _sessions(final)

    known = {s["session_id"]: s for s in sessions}
    for event in events:
        assert event["judge_session_id"] in known, \
            f"{event['step']} cites a session that is not recorded"
        entry = known[event["judge_session_id"]]
        assert entry["step_models"][event["step"]] == event["model"], \
            "the session and the verdict must agree about the model"


def test_an_artifact_resume_gets_a_new_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with judged_by(ModelNamingJudge("first-model")):
            first = _run(root)
        first_ids = {e["judge_session_id"] for e in _judge_events(first)}

        with judged_by(ModelNamingJudge("second-model")):
            second = _run(root, resume_run_id=first["run_id"])
        sessions = _sessions(first)
        after = _judge_events(second)

    ids = [s["session_id"] for s in sessions]
    assert len(set(ids)) == 2, "two executions, two ids"
    assert first_ids == {ids[0]}
    # Whatever this pass judged cites the second session, never the first.
    resumed_ids = {e["judge_session_id"] for e in after} - first_ids
    assert resumed_ids <= {ids[1]}


def test_a_checkpoint_continue_gets_a_new_session_id():
    from src.run import continue_workflow

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with judged_by(ModelNamingJudge("started-it")):
            paused = run_workflow(
                project="provenance",
                input_bundle={"paths": [str(fixtures.bundle_for(
                    {"input_type": "matrix", "matrix_kind": "filtered"}, root / "bundle"))]},
                config={"species": "human",
                        "transcriptome": str(fixtures.make_reference(
                            root, "ref", genomes=["GRCh38"])),
                        "min_genes": 1, "max_pct_mito": 100},
                runs_dir=str(root / "runs"),
                policy=GatePolicy(interactive=True),
                checkpointer_kind="sqlite",
                decide=None,
            )
        assert paused["status"] == "needs_review", "precondition: it stopped at a gate"
        before = {e["judge_session_id"] for e in _judge_events(paused)}

        with judged_by(ModelNamingJudge("finished-it")):
            continue_workflow(
                run_id=paused["run_id"], runs_dir=str(root / "runs"),
                policy=GatePolicy(interactive=True),
                decide=lambda _request: {"decision": "accept", "rationale": ""},
            )
        sessions = _sessions(paused)
        events = _judge_events(paused)

    ids = [s["session_id"] for s in sessions]
    assert [s["mode"] for s in sessions] == ["new", "checkpoint_continue"]
    assert len(set(ids)) == 2, "continuing is a second session, not the first one again"

    cited_after = {e["judge_session_id"] for e in events} - before
    assert cited_after == {ids[1]}, "verdicts scored after the gate cite the new session"
    scored_by = {e["judge_session_id"]: e["model"] for e in events}
    assert scored_by[ids[0]] == "started-it"
    assert scored_by[ids[1]] == "finished-it"


def test_the_stub_gets_a_session_id_and_still_reports_no_model():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp))
        sessions = _sessions(final)
        events = _judge_events(final)

    assert sessions[0]["session_id"].startswith("js-")
    assert sessions[0]["backend"] == "stub"
    for event in events:
        assert event["judge_session_id"] == sessions[0]["session_id"]
        assert event["model"] is None, "a session id is not a claim that a model ran"


def test_a_session_id_carries_no_endpoint_key_or_other_secret():
    """The fingerprint is taken over what decides a verdict, not over where it ran."""
    described = describe_judge(
        LocalLLMJudge(model="gpt-oss:120b", api_key=SECRET,
                      base_url="https://someone:hunter2@lab.example:11434/v1"),
        STEPS,
    )
    identifier = judge_session_id(0, described)

    for forbidden in (SECRET, "hunter2", "someone", "lab.example", "11434", "gpt-oss"):
        assert forbidden not in identifier, f"{forbidden!r} leaked into {identifier!r}"
    assert re.fullmatch(r"js-\d{2}-[0-9a-f]{12}", identifier), identifier


def test_the_same_configuration_fingerprints_the_same_and_a_new_prompt_does_not():
    """The case the model name alone cannot tell apart."""
    with prompts("base prompt", {"run_qc_metrics": "the addendum"}):
        first = describe_judge(LocalLLMJudge(model="m", api_key="x"), STEPS)
        again = describe_judge(LocalLLMJudge(model="m", api_key="x"), STEPS)
    with prompts("base prompt", {"run_qc_metrics": "the addendum, reworded"}):
        reworded = describe_judge(LocalLLMJudge(model="m", api_key="x"), STEPS)

    assert session_fingerprint(first) == session_fingerprint(again)
    assert session_fingerprint(first) != session_fingerprint(reworded), \
        "same model, different prompt — the whole reason the id is not just the model"
    assert judge_session_id(0, first) != judge_session_id(0, reworded)


def test_the_endpoint_alone_does_not_change_the_fingerprint():
    """Serving the same model from a second machine is not a different judge."""
    with prompts("base", {}):
        here = describe_judge(
            LocalLLMJudge(model="m", api_key="x", base_url="http://localhost:11434/v1"), STEPS)
        there = describe_judge(
            LocalLLMJudge(model="m", api_key="x", base_url="http://dgx.lab:11434/v1"), STEPS)

    assert here["endpoint"] != there["endpoint"], "and both are still recorded"
    assert session_fingerprint(here) == session_fingerprint(there)


def test_two_identical_configurations_in_one_run_still_get_different_ids():
    """The index is what makes it unique; the fingerprint is what makes it readable."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root)
        _run(root, resume_run_id=first["run_id"])
        sessions = _sessions(first)

    assert sessions[0]["session_id"] != sessions[1]["session_id"]
    assert sessions[0]["session_id"].split("-")[-1] == \
        sessions[1]["session_id"].split("-")[-1], "identical judge, identical fingerprint"


# --- 8. nothing secret ---------------------------------------------------------------------


def test_no_api_key_reaches_the_recorded_provenance():
    described = describe_judge(
        LocalLLMJudge(model="m", api_key=SECRET, base_url="http://host:11434/v1"), STEPS
    )
    assert SECRET not in json.dumps(described)
    assert "api_key" not in json.dumps(described)


def test_credentials_embedded_in_the_endpoint_are_stripped():
    """The URL form allows them, and this file is written beside shared results."""
    described = describe_judge(
        LocalLLMJudge(model="m", api_key="x",
                      base_url="https://someone:hunter2@lab.example/v1"), STEPS
    )
    assert described["endpoint"] == "https://lab.example/v1"
    assert "hunter2" not in json.dumps(described)
    assert "someone" not in json.dumps(described)


def test_a_whole_run_metadata_file_carries_no_secret():
    import os

    original = os.environ.get("SCRNA_JUDGE_API_KEY")
    os.environ["SCRNA_JUDGE_API_KEY"] = SECRET
    try:
        with tempfile.TemporaryDirectory() as tmp:
            final = _run(Path(tmp))
            raw = Path(final["run_metadata_path"]).read_text(encoding="utf-8")
    finally:
        if original is None:
            os.environ.pop("SCRNA_JUDGE_API_KEY", None)
        else:
            os.environ["SCRNA_JUDGE_API_KEY"] = original

    assert SECRET not in raw
    for forbidden in ("api_key", "authorization", "bearer", "token"):
        assert forbidden not in raw.lower(), f"{forbidden!r} appears in the metadata"


# --- an unfamiliar judge is still recorded ---------------------------------------------------


def test_a_judge_that_cannot_describe_itself_is_named_rather_than_skipped():
    class Homegrown:
        def judge(self, step, payload):  # pragma: no cover - never called
            raise NotImplementedError

    described = describe_judge(Homegrown(), STEPS)
    assert described["backend"] == "Homegrown"
    assert "does not describe itself" in described["note"]


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

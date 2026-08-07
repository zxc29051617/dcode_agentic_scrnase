"""Tests for `src/judge.py`, the layer that turns a step's numbers into a verdict.

This had no test file at all, which is awkward given it is the part of the
project that is not just a Scanpy pipeline. `StubJudge` was exercised through
the graph; `LocalLLMJudge` had been verified once by hand against a real
endpoint and never since.

No model is contacted here. The interesting behaviour is not what a model says
— that varies — but what happens around it: that a reply which does not fit the
schema is rejected rather than believed, that a model wrapping its JSON in
prose is still understood, and that a `step` cannot be relabelled by the thing
being judged.

Run with `python tests/test_judge.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import judge as judge_module  # noqa: E402
from src.judge import JudgeResult, LocalLLMJudge, StubJudge, get_judge  # noqa: E402

VALID = {
    "step": "run_pca", "verdict": "warn", "score": 60,
    "reasons": ["variance is low"], "evidence": {"cumulative": 0.18},
    "suggested_action": "check the elbow", "needs_human_review": True,
}


def _payload(**overrides):
    payload = {"step": "run_pca", "status": "ok", "warnings": [], "errors": [],
               "output": {}, "metrics": {}}
    payload.update(overrides)
    return payload


# --- the contract ----------------------------------------------------------------


def test_a_verdict_outside_the_three_words_is_rejected():
    try:
        JudgeResult(**{**VALID, "verdict": "probably fine"})
    except Exception:
        return
    raise AssertionError("only pass/warn/fail may be a verdict")


def test_a_score_outside_the_scale_is_rejected():
    for bad in (-1, 101):
        try:
            JudgeResult(**{**VALID, "score": bad})
        except Exception:
            continue
        raise AssertionError(f"score {bad} should not validate")


def test_an_extra_field_is_rejected_rather_than_carried():
    """A model inventing a field must not smuggle it into state."""
    try:
        JudgeResult(**{**VALID, "recommended_thresholds": {"min_genes": 200}})
    except Exception:
        return
    raise AssertionError("the schema forbids extra fields")


def test_the_schema_on_disk_and_the_model_agree():
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "judge_result.schema.json"
    if not schema_path.exists():
        return
    published = set(json.loads(schema_path.read_text(encoding="utf-8")).get("properties", {}))
    assert set(JudgeResult.model_fields) == published


# --- StubJudge: the offline backend ------------------------------------------------


def test_a_clean_step_passes():
    verdict = StubJudge().judge("run_pca", _payload())
    assert verdict.verdict == "pass" and verdict.needs_human_review is False


def test_warnings_become_warn_and_ask_for_a_person():
    verdict = StubJudge().judge("run_pca", _payload(warnings=["something odd"]))
    assert verdict.verdict == "warn"
    assert verdict.needs_human_review is True
    assert "something odd" in verdict.reasons


def test_errors_become_fail():
    verdict = StubJudge().judge("run_pca", _payload(status="error", errors=["it broke"]))
    assert verdict.verdict == "fail" and verdict.score == 0


def test_a_scaffold_scores_zero_so_it_cannot_read_as_a_pass():
    verdict = StubJudge().judge("run_pca", _payload(status="scaffold"))
    assert verdict.verdict == "pass", "a scaffold does not block the wiring check"
    assert verdict.score == 0
    assert any("SCAFFOLD" in reason for reason in verdict.reasons)


def test_the_stub_makes_no_claim_about_the_data():
    """It reads status and messages only — never a number from the analysis."""
    verdict = StubJudge().judge("run_pca", _payload(metrics={"cumulative": 0.02}))
    assert verdict.verdict == "pass", "the stub cannot tell 2% variance is bad"
    assert set(verdict.evidence) == {"status", "n_warnings", "n_errors"}


# --- LocalLLMJudge: everything around the model -------------------------------------


class _FakeLLM:
    """Stands in for ChatOpenAI: no endpoint, scripted replies."""

    def __init__(self, structured=None, raw=None, structured_raises=False):
        self._structured, self._raw = structured, raw
        self._structured_raises = structured_raises

    def with_structured_output(self, _schema):
        if self._structured_raises:
            class Failing:
                def invoke(self, _messages):
                    raise RuntimeError("this model has no tool calling")
            return Failing()
        outer = self

        class Structured:
            def invoke(self, _messages):
                return outer._structured
        return Structured()

    def invoke(self, _messages):
        class Response:
            content = self._raw
        return Response()


def _local(llm) -> LocalLLMJudge:
    """A LocalLLMJudge with its client swapped, so nothing is dialled."""
    instance = LocalLLMJudge.__new__(LocalLLMJudge)
    instance.system_prompt = judge_module.PROMPT_PATH.read_text(encoding="utf-8")
    instance.llm = llm
    return instance


def test_a_structured_reply_is_used_directly():
    verdict = _local(_FakeLLM(structured=JudgeResult(**VALID))).judge("run_pca", _payload())
    assert verdict.verdict == "warn" and verdict.score == 60


def test_the_step_is_stamped_by_us_not_by_the_model():
    """A model naming a different step would misfile the verdict."""
    lying = JudgeResult(**{**VALID, "step": "some_other_step"})
    verdict = _local(_FakeLLM(structured=lying)).judge("run_pca", _payload())
    assert verdict.step == "run_pca"


def test_a_model_without_tool_calling_falls_back_to_parsing_json():
    llm = _FakeLLM(structured_raises=True, raw=json.dumps(VALID))
    verdict = _local(llm).judge("run_pca", _payload())
    assert verdict.verdict == "warn"


def test_json_wrapped_in_prose_and_fences_is_still_read():
    """Small local models rarely return bare JSON."""
    raw = "Sure! Here is my assessment:\n```json\n" + json.dumps(VALID) + "\n```\nHope that helps."
    verdict = _local(_FakeLLM(structured_raises=True, raw=raw)).judge("run_pca", _payload())
    assert verdict.verdict == "warn"


def test_a_reply_that_does_not_fit_the_schema_raises_rather_than_being_believed():
    """The node above turns this into a failing verdict; silently accepting it
    would put an unvalidated model answer into the audit log."""
    bad = json.dumps({**VALID, "verdict": "looks good to me"})
    try:
        _local(_FakeLLM(structured_raises=True, raw=bad)).judge("run_pca", _payload())
    except Exception:
        return
    raise AssertionError("an off-schema verdict must not be accepted")


def test_a_reply_with_no_json_at_all_raises():
    llm = _FakeLLM(structured_raises=True, raw="I am not able to help with that.")
    try:
        _local(llm).judge("run_pca", _payload())
    except Exception:
        return
    raise AssertionError("prose with no verdict is not a verdict")


def test_the_prompt_demands_numbers_from_the_payload():
    """Without this the models paraphrased the warning instead of reading."""
    prompt = judge_module.PROMPT_PATH.read_text(encoding="utf-8")
    assert "cite a number" in prompt.lower()
    assert "0-100" in prompt or "0–100" in prompt


# --- advice: a suggestion, never an instruction ----------------------------------------


def test_a_verdict_carries_no_advice_by_default():
    """Most steps have nothing to choose; inventing a number there is noise."""
    assert JudgeResult(**VALID).advice == []
    assert StubJudge().judge("run_pca", _payload()).advice == []


def test_advice_rides_on_the_verdict_rather_than_arriving_separately():
    advised = {**VALID, "advice": [
        {"parameter": "max_pct_mito", "suggested_value": 15,
         "rationale": "median is 5.4; a 5% cut removes 54.6%", "confidence": "medium"}
    ]}
    verdict = _local(_FakeLLM(structured=JudgeResult(**advised))).judge("run_pca", _payload())
    assert verdict.advice[0].parameter == "max_pct_mito"
    assert verdict.advice[0].suggested_value == 15
    assert verdict.advice[0].confidence == "medium"


def test_a_made_up_confidence_is_rejected():
    """`very high` is the model editorialising; the scale is fixed."""
    try:
        JudgeResult(**{**VALID, "advice": [
            {"parameter": "x", "suggested_value": 1, "confidence": "very high"}
        ]})
    except Exception:
        return
    raise AssertionError("confidence is low/medium/high only")


def test_advice_defaults_to_low_confidence():
    """Unqualified suggestions should read as tentative, not endorsed."""
    verdict = JudgeResult(**{**VALID, "advice": [{"parameter": "n_comps"}]})
    assert verdict.advice[0].confidence == "low"


def test_advice_cannot_smuggle_extra_fields():
    try:
        JudgeResult(**{**VALID, "advice": [
            {"parameter": "x", "suggested_value": 1, "apply": True}
        ]})
    except Exception:
        return
    raise AssertionError("an advice entry may not carry an `apply` flag")


def test_the_prompt_tells_the_model_advice_is_not_applied():
    prompt = judge_module.PROMPT_PATH.read_text(encoding="utf-8")
    lowered = prompt.lower()
    assert "not applying anything" in lowered
    assert "0–100" in prompt or "0-100" in prompt, "the units trap must be spelled out"
    assert "empty list" in lowered, "most steps have nothing to advise on"


# --- choosing a backend --------------------------------------------------------------


def test_stub_is_the_default_so_tests_never_need_a_model():
    assert isinstance(get_judge(), StubJudge)
    assert isinstance(get_judge("stub"), StubJudge)


def test_an_unknown_backend_is_refused_rather_than_defaulted():
    try:
        get_judge("gpt-9")
    except ValueError as exc:
        assert "unknown judge backend" in str(exc)
        return
    raise AssertionError("an unknown backend must not quietly become the stub")


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

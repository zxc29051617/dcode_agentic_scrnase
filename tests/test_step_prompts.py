"""Tests for `prompts/steps/*.md` — the per-step judge instructions.

These files are a second description of what each step produces, which is the
shape of problem this project keeps having to clean up. They cannot be
generated from the code, because the judgement written into them is the whole
point, so they are checked against it instead: every field a prompt names has
to exist in the step that produces it.

The failure being guarded is silent. A prompt citing `per_cluster.confidence`
when the field is `median_conf_score` raises nothing — the judge looks, finds
no such key, and reports accurately on what it did find. The prompt reads fine
and does nothing.

Run with `python tests/test_step_prompts.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.judge import STEP_PROMPT_DIR  # noqa: E402
from src.registry import REGISTRY, SKILLS_DIR, load_skill  # noqa: E402

REQUIRED_SECTIONS = (
    "### What to judge",
    "### What the numbers cannot show",
    "### Worked examples",
    "### When to warn",
)

#: A backticked token has to look like a payload field before it is checked:
#: lower-case, at least one underscore or dot, nothing else. That skips prose
#: in backticks (`pass`, `warn`) and cell type names, and keeps the false
#: positives to zero at the cost of not checking single-word field names.
FIELD_TOKEN = re.compile(r"`([a-z][a-z0-9_]*(?:[._][a-z0-9_]+)+)`")

#: Words that look like fields but belong to the judge's own vocabulary or the
#: pipeline's, not to any one step's output.
NOT_A_FIELD = {
    "suggested_action", "output_is_abridged", "run_dir", "step_results",
    "judge_results", "human_decisions", "local_judge_base.md", "run_metadata.json",
    "audit.jsonl", "markers.csv", "output.json", "adata.h5ad", "report.html",
}


def _prompts() -> list[Path]:
    if not STEP_PROMPT_DIR.exists():
        return []
    return sorted(p for p in STEP_PROMPT_DIR.glob("*.md") if p.name != "README.md")


def test_there_is_at_least_one_to_check():
    """A green suite that checked nothing would be worse than a red one."""
    assert _prompts(), f"no step prompts found in {STEP_PROMPT_DIR}"


def test_every_prompt_names_a_real_step():
    """A file named for a step that does not exist is never loaded, silently."""
    for path in _prompts():
        assert path.stem in REGISTRY, \
            f"prompts/steps/{path.name} matches no registry step"


def test_every_prompt_is_for_a_step_that_has_a_judge():
    """Only judged steps read a prompt; one for `human_review_decision` is dead."""
    for path in _prompts():
        assert REGISTRY[path.stem].judge, \
            f"{path.name} is for a step with no judge, so nothing will ever read it"


def test_every_prompt_opens_by_naming_its_step():
    """The text is appended to a shared prompt; unscoped, it reads as general."""
    for path in _prompts():
        first = path.read_text(encoding="utf-8").lstrip().splitlines()[0]
        assert f"`{path.stem}`" in first, \
            f"{path.name} should open by naming `{path.stem}`, got: {first!r}"


def test_every_prompt_has_the_four_sections():
    for path in _prompts():
        text = path.read_text(encoding="utf-8")
        missing = [s for s in REQUIRED_SECTIONS if s not in text]
        assert not missing, f"{path.name} is missing {missing}"


def test_the_blind_spot_section_is_not_left_empty():
    """The section most likely to be skipped, and the one that did the work."""
    heading = "### What the numbers cannot show"
    for path in _prompts():
        text = path.read_text(encoding="utf-8")
        # A missing heading is the other test's failure; reporting it twice
        # buries the real one, and indexing a substring that is not there
        # crashes the run instead of failing it.
        if heading not in text:
            continue
        rest = text.split(heading, 1)[1]
        body = rest.split("###", 1)[0].strip()
        assert len(body) > 120, \
            (f"{path.name}: '{heading}' is {len(body)} chars. "
             "Name the blind spot concretely — this is the section that changed "
             "the measured result.")


def _implementation_text(step: str) -> str:
    """The step's source plus its SKILL.md — everywhere a field name could live."""
    parts = []
    for name in (f"{step}.py", "SKILL.md"):
        path = SKILLS_DIR / step / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _unknown_fields(text: str, step: str) -> list[str]:
    """Field names the prompt cites that do not appear in the step at all.

    Matching is on word boundaries, not substrings. `median_conf` is a
    substring of the real `median_conf_score`, so a substring test accepts a
    prompt naming a field that does not exist — which is the exact drift being
    guarded against. `_` counts as a word character, so `\\bmedian_conf\\b`
    does not match inside `median_conf_score`.
    """
    source = _implementation_text(step)
    unknown = []
    for token in sorted(set(FIELD_TOKEN.findall(text))):
        if token in NOT_A_FIELD:
            continue
        # Naming another step is what a prompt should do when it explains why a
        # value matters downstream — `find_markers` cannot rank genes for a
        # cluster of three. Those are steps, not fields of this one.
        if token in REGISTRY:
            continue
        # Dotted paths are checked segment by segment: `per_cluster.flags` is
        # right only if both names appear in the step.
        parts = token.split(".")
        if all(re.search(rf"\b{re.escape(part)}\b", source) for part in parts):
            continue
        unknown.append(token)
    return unknown


def test_every_field_a_prompt_names_exists_in_its_step():
    """The anti-drift check. Rename a field, and its prompt fails here."""
    for path in _prompts():
        step = path.stem
        assert _implementation_text(step), f"no implementation found for {step}"
        unknown = _unknown_fields(path.read_text(encoding="utf-8"), step)
        assert not unknown, (
            f"{path.name} names {unknown}, which do not appear in "
            f"skills/{step}/. Either the prompt is citing a field that no "
            f"longer exists, or the step was renamed without its prompt.")


def test_the_drift_check_would_actually_catch_drift():
    """A check that only ever passes is indistinguishable from no check.

    Two shapes of the real failure: a field renamed out from under a prompt,
    and a plausible-looking field that never existed.
    """
    step = "cross_check_annotation"
    renamed = _unknown_fields("cite `per_cluster.median_conf` here", step)
    assert renamed == ["per_cluster.median_conf"], \
        f"a renamed field slipped through: {renamed}"

    invented = _unknown_fields("look at `cluster_purity_score`", step)
    assert invented == ["cluster_purity_score"], \
        f"an invented field slipped through: {invented}"

    real = _unknown_fields("compare `celltypist_label` to `database_candidates`", step)
    assert real == [], f"a real field was rejected: {real}"


def test_a_prompt_that_names_nothing_is_suspicious():
    """A prompt with no field references cannot ask the judge to cite evidence."""
    for path in _prompts():
        text = path.read_text(encoding="utf-8")
        found = [t for t in FIELD_TOKEN.findall(text) if t not in NOT_A_FIELD]
        assert found, (
            f"{path.name} names no payload fields. The judge is asked to quote "
            "the values it used, and it cannot do that without their names.")


def test_the_prompts_are_reachable_through_the_judge():
    """The files are only useful if `system_prompt_for` actually finds them."""
    from src.judge import JudgeResult, LocalLLMJudge, PROMPT_PATH

    instance = LocalLLMJudge.__new__(LocalLLMJudge)
    instance.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    instance._step_prompts = {}

    for path in _prompts():
        combined = instance.system_prompt_for(path.stem)
        assert combined != instance.system_prompt, \
            f"{path.name} exists but system_prompt_for({path.stem!r}) ignored it"
        assert combined.startswith(instance.system_prompt), \
            "the base prompt must survive intact"
    assert JudgeResult  # imported to prove the module loads as the judge does


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

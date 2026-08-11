"""The documents cannot say things the registry does not.

Written after `skills/README.md` was found listing nineteen `judge_*` skills that
had been deleted, omitting eight that existed, calling every skill a "scaffold"
long after they were implemented, and telling the reader the orchestrator calls
them over MCP, which it never has. Nobody was careless. The file simply had no
way to fail, and a document that cannot fail drifts until somebody trusts it.

Two directions are checked, because they catch different mistakes:

  generator  what the docs *should* contain, from `src/registry.py`
  reality    that every step named actually has a folder on disk

Run with `python tests/test_registry_docs.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import export_registry_docs as generator  # noqa: E402
from src.registry import REGISTRY, SKILLS_DIR  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_README = PROJECT_ROOT / "skills" / "README.md"
TOOL_REGISTRY = PROJECT_ROOT / "docs" / "tool_registry.md"


# --- every registry step is real ---------------------------------------------------


def test_every_registry_step_has_an_implementation():
    """A step the graph will call and a folder that does not exist is a crash."""
    missing = generator.missing_skills()
    assert missing == [], f"registry steps with no skills/<name>/<name>.py: {missing}"


def test_every_registry_step_has_a_contract():
    absent = [
        step for step in REGISTRY
        if not (SKILLS_DIR / step / "SKILL.md").exists()
    ]
    assert absent == [], f"registry steps with no SKILL.md: {absent}"


def test_no_skill_folder_is_left_behind_by_a_deleted_step():
    """The failure that produced nineteen phantom judge folders, from the other side."""
    orphans = generator.orphan_skills()
    assert orphans == [], f"skill folders no registry step names: {orphans}"


# --- the docs match the registry -----------------------------------------------------


def test_the_generated_sections_are_current():
    """`--check` in one call, so a stale doc fails here as well as in CI."""
    assert generator.main(["--check"]) == 0, (
        "a generated section is stale; run: python scripts/export_registry_docs.py"
    )


def test_the_skills_readme_lists_exactly_the_registry_steps():
    listed = set(re.findall(r"^- \[`([a-z_0-9]+)`\]", SKILLS_README.read_text(encoding="utf-8"),
                            flags=re.MULTILINE))
    assert listed == set(REGISTRY), (
        f"only in the README: {sorted(listed - set(REGISTRY))}; "
        f"only in the registry: {sorted(set(REGISTRY) - listed)}"
    )


def test_the_registry_table_lists_exactly_the_registry_steps():
    body = _section(TOOL_REGISTRY.read_text(encoding="utf-8"), "registry-table")
    listed = re.findall(r"^\| \d+ \| `([a-z_0-9]+)` \|", body, flags=re.MULTILINE)
    assert listed == list(REGISTRY), "the table and the registry disagree, or are out of order"


def test_the_count_is_computed_rather_than_typed():
    """`26` was written by hand in three places and wrong in two of them."""
    rendered = generator.render_skill_list()
    assert f"**{len(REGISTRY)} skills**" in rendered
    assert str(len(REGISTRY)) in generator.render_registry_table()


def test_the_judge_column_is_the_node_name_not_a_folder():
    body = generator.render_registry_table()
    for step, spec in REGISTRY.items():
        if spec.judge:
            assert f"`{spec.judge}`" in body, f"{step}'s judge node is missing from the table"
            assert not (SKILLS_DIR / spec.judge).exists(), (
                f"{spec.judge} is a graph node name; a folder by that name would be the "
                f"design this project dropped"
            )


# --- the phantom skills stay gone -------------------------------------------------------


def test_no_document_advertises_a_skill_that_does_not_exist():
    """Anything written as a skill folder link has to be one."""
    for document in (SKILLS_README, TOOL_REGISTRY):
        text = document.read_text(encoding="utf-8")
        for name in re.findall(r"\[`([a-z_0-9]+)`\]\(\1/SKILL\.md\)", text):
            assert (SKILLS_DIR / name / "SKILL.md").exists(), (
                f"{document.name} links {name}/SKILL.md, which does not exist"
            )


def test_the_readme_does_not_list_judge_skills():
    listed = set(re.findall(r"^- \[`([a-z_0-9]+)`\]", SKILLS_README.read_text(encoding="utf-8"),
                            flags=re.MULTILINE))
    assert not {name for name in listed if name.startswith("judge_")}, (
        "judging is one implementation in src/judge.py, not a skill per step"
    )


def test_no_document_still_claims_the_wiring_is_generated_from_the_registry():
    """It never has been: a conditional edge is a predicate, not a table row."""
    text = TOOL_REGISTRY.read_text(encoding="utf-8")
    assert "the wiring is generated from the registry" not in text
    assert "`src/graph.py`**" in text, "the doc has to say where the edges actually live"


# --- the generator itself ----------------------------------------------------------------


def test_the_same_registry_renders_the_same_bytes():
    """A generator that reorders on every run makes a diff nobody can read."""
    assert generator.render_skill_list() == generator.render_skill_list()
    assert generator.render_registry_table() == generator.render_registry_table()


def test_rendering_a_file_twice_is_a_no_op():
    """Running the generator on its own output has to change nothing."""
    for document in (SKILLS_README, TOOL_REGISTRY):
        once = generator.render_file(document)
        twice = generator.replace_section(
            once,
            "skill-list" if document is SKILLS_README else "registry-table",
            (generator.render_skill_list if document is SKILLS_README
             else generator.render_registry_table)(),
        )
        assert once == twice, f"{document.name} is not stable under a second pass"


def test_only_the_marked_section_is_rewritten():
    original = "before\n" + generator.BEGIN.format(name="x") + "\nold\n" \
        + generator.END.format(name="x") + "\nafter\n"
    updated = generator.replace_section(original, "x", "new")
    assert updated.startswith("before\n")
    assert updated.endswith("\nafter\n")
    assert "new" in updated and "old" not in updated


def test_a_missing_marker_is_an_error_rather_than_a_silent_no_op():
    try:
        generator.replace_section("no markers here", "skill-list", "body")
    except SystemExit as exc:
        assert "missing markers" in str(exc)
    else:
        raise AssertionError("a document without markers must not be silently skipped")


def _section(text: str, name: str) -> str:
    begin, end = generator.BEGIN.format(name=name), generator.END.format(name=name)
    return text.split(begin, 1)[1].split(end, 1)[0]


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

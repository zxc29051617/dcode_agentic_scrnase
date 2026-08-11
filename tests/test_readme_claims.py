"""Claims the README is not allowed to make again.

Each of these was written in good faith, was true or nearly true once, and went
stale silently. A number in prose has no way to fail, so the fix is not to
correct it — the corrected number goes stale too — but to stop the file from
carrying that kind of claim at all and to point at the generated artefact
instead.

The three:

  - "the wiring is generated from the registry, you need not touch graph.py".
    Never true. `assert_registry_covered()` rejects a step that was registered
    and not wired, which is the mechanism that makes the claim testably false.
  - "54 nodes / 110 edges". The graph has moved twice since; it is 56 and 113
    today, and will move again.
  - "597 pass / 0 fail / 20 skip". Written as a snapshot, with a caveat saying
    it would go stale, which is a way of describing the problem rather than
    fixing it.

Run with `python tests/test_readme_claims.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph import build_graph  # noqa: E402
from src.judge import StubJudge  # noqa: E402
from src.registry import REGISTRY, StepSpec  # noqa: E402
from src import registry as registry_module  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
README = PROJECT_ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


# --- the wiring is not generated ---------------------------------------------------------


def test_a_registered_step_that_is_not_wired_is_rejected():
    """The fact the README contradicted, asserted directly.

    If this ever starts passing silently, the README's old claim becomes true
    and the test below should be deleted rather than worked around.
    """
    registry_module.REGISTRY["a_step_nobody_wired"] = StepSpec(
        "a_step_nobody_wired", "analysis", "judge_nobody"
    )
    try:
        build_graph(judge=StubJudge())
    except AssertionError as exc:
        assert "a_step_nobody_wired" in str(exc)
    else:
        raise AssertionError(
            "build_graph accepted a step with no edges; the registry does not "
            "generate topology and something has started pretending it does"
        )
    finally:
        registry_module.REGISTRY.pop("a_step_nobody_wired", None)


def test_the_readme_does_not_claim_the_wiring_is_generated():
    text = _readme()
    for claim in ("接線是從 registry 生成的", "不用動 `graph.py`",
                  "wiring is generated from the registry"):
        assert claim not in text, f"the README claims {claim!r}, which is false"


def test_the_readme_says_where_topology_actually_lives():
    text = _readme()
    assert "src/graph.py" in text or "`graph.py`" in text
    assert "assert_registry_covered" in text, (
        "the README should name the mechanism that catches an unwired step"
    )


# --- no hand-written graph size ------------------------------------------------------------


def test_the_readme_does_not_hand_write_the_node_or_edge_count():
    """`54 node / 110 edge` was two topology changes out of date."""
    text = _readme()
    offenders = re.findall(r"\b\d+\s*(?:個\s*)?(?:node|edge|nodes|edges)\b", text)
    assert offenders == [], (
        f"the README hand-writes graph sizes {offenders}; "
        f"link docs/graph.mmd instead, which export_graph.py keeps current"
    )


def test_the_readme_points_at_the_generated_graph():
    assert "docs/graph.mmd" in _readme()


def test_the_generated_graph_is_the_one_that_would_be_produced_now():
    """Nothing here should have changed the topology, and this says so."""
    exported = (PROJECT_ROOT / "docs" / "graph.mmd").read_text(encoding="utf-8")
    current = build_graph(judge=StubJudge()).get_graph().draw_mermaid()
    assert exported == current, "docs/graph.mmd is stale; run scripts/export_graph.py"


# --- no pass/skip baseline in prose ----------------------------------------------------------


def test_the_readme_does_not_record_a_pass_or_skip_count():
    """A snapshot with a caveat about going stale is still a snapshot."""
    text = _readme()
    patterns = [
        r"\b\d{2,4}\s*(?:個)?\s*(?:tests?|測試)\s*(?:pass|通過)",
        r"\b\d{2,4}\s+pass\b",
        r"\bpass(?:ed)?\s*[:：]\s*\d{2,4}\b",
        r"\b\d{1,4}\s+skip(?:ped)?\b",
        r"\b\d{2,4}\s*/\s*0\s*fail",
    ]
    found = [match for pattern in patterns for match in re.findall(pattern, text)]
    assert found == [], (
        f"the README states a test-count baseline {found}; it goes stale on the "
        f"next commit. State what does not change and link the badge."
    )


def test_the_readme_still_states_what_does_not_change():
    """Removing the numbers must not remove the standard they were serving."""
    text = _readme()
    assert "badge.svg" in text, "the badge is what replaces a written number"
    assert "0 fail" in text or "沒有 failure" in text
    assert "skip" in text and ("缺" in text or "absent" in text), (
        "the rule that a skip must name the data it wants has to survive"
    )


def test_every_registry_step_is_still_covered():
    """Guards the test above from passing because the graph stopped being checked."""
    graph = build_graph(judge=StubJudge()).get_graph()
    names = set(graph.nodes)
    missing = [step for step in REGISTRY if step not in names]
    assert missing == [], missing


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

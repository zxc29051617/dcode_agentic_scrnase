"""Every step the executor can run has a sentence a person can read.

`apps/web/lib/stepLabels.ts` is a second list of the pipeline's steps, kept
outside `src/registry.py` on purpose: the registry is imported by every skill
and has no business carrying web copy, and a typo on a page should not mean
editing the scientific package.

The price of a second list is that it can fall behind, so this file is what
stops it. A step added to the registry fails these until somebody writes the
sentence a person will read at its gate — which is the point, because the
fallback renders `run_scvi` as "Run scvi", and that is a worse thing to ship
quietly than a failing test is to fix.

Read as text rather than executed: this suite has no Node, and the shape being
checked — which keys exist — survives a regex honestly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import REGISTRY  # noqa: E402

LABELS = Path(__file__).resolve().parent.parent / "apps" / "web" / "lib" / "stepLabels.ts"

#: `STEP_LABELS` entry keys. Bare identifiers at one indent level inside the
#: object, which is how every entry in that file is written.
ENTRY = re.compile(r"^  (\w+): \{$", re.M)


def _source() -> str:
    return LABELS.read_text(encoding="utf-8")


def _labelled() -> set[str]:
    body = _source().split("export const STEP_LABELS", 1)[1].split("\n};", 1)[0]
    return set(ENTRY.findall(body))


def test_every_registry_step_has_a_plain_language_label():
    missing = sorted(set(REGISTRY) - _labelled())
    assert not missing, (
        f"{len(missing)} step(s) would render as a function name: {missing}. "
        f"Add them to {LABELS.name}."
    )


def test_no_label_names_a_step_that_does_not_exist():
    """A renamed step leaves its old copy behind, describing nothing."""
    extra = sorted(_labelled() - set(REGISTRY))
    assert not extra, f"{LABELS.name} labels step(s) the registry does not have: {extra}"


def test_a_title_is_not_the_function_name_with_the_underscores_taken_out():
    """That is what the fallback already does. An entry that only does that is
    a placeholder somebody meant to come back to."""
    source = _source()
    lazy = []
    for step in REGISTRY:
        m = re.search(rf"^  {step}: \{{\n    title: \"([^\"]+)\"", source, re.M)
        if not m:
            continue
        title = m.group(1)
        if title.lower().replace(" ", "_") == step:
            lazy.append(step)
    assert not lazy, f"these titles are just the function name respelled: {lazy}"


def test_every_step_says_what_it_does_to_the_data():
    source = _source()
    thin = []
    for step in REGISTRY:
        m = re.search(rf"^  {step}: \{{\n    title: \"[^\"]+\",\n    what:\s*\n?\s*\"([^\"]+)\"", source, re.M)
        if not m or len(m.group(1)) < 40:
            thin.append(step)
    assert not thin, f"these have no usable one-line description: {thin}"


def test_the_destructive_step_says_so():
    """`apply_cell_qc_filter` throws cells away and cannot be undone later in
    the run. Somebody reading its gate has to learn that from the screen."""
    source = _source()
    block = re.search(r"^  apply_cell_qc_filter: \{(.*?)^  \},", source, re.S | re.M)
    assert block, "apply_cell_qc_filter has no entry"
    assert "destructive" in block.group(1).lower() or "cannot be undone" in block.group(1).lower()


def test_the_long_step_warns_that_it_goes_quiet():
    """`cellranger_count` runs for tens of minutes writing nothing. Every
    person who has watched it has wondered whether it broke."""
    source = _source()
    block = re.search(r"^  cellranger_count: \{(.*?)^  \},", source, re.S | re.M)
    assert block, "cellranger_count has no entry"
    text = block.group(1).lower()
    assert "minutes" in text and ("nothing" in text or "quiet" in text or "silent" in text)


def test_warn_is_not_worded_as_an_error():
    """The judge returns `warn` on a step that ran soundly. A person at a gate
    reading it as "something went wrong" makes a different decision than the
    evidence supports — this is the wording that misled a real reader."""
    source = _source()
    block = re.search(r"warn: \{(.*?)\},", source, re.S)
    assert block, "no wording recorded for the `warn` verdict"
    assert "not an error" in block.group(1).lower()


def test_the_statuses_the_gateway_emits_are_all_worded():
    """Including `interrupted`, which exists because four runs in this
    project's own `runs/` reported `running` for hours after their processes
    were gone."""
    # Matched on the key alone, not on the layout after it: an entry long
    # enough to wrap onto three lines is still an entry.
    block = _source().split("STATUS_WORDS", 1)[1].split("\n};", 1)[0]
    worded = set(re.findall(r"^  (\w+): \{", block, re.M))
    for status in ("running", "needs_review", "interrupted", "completed", "failed", "halted"):
        assert status in worded, f"{status!r} has no plain-language wording"


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

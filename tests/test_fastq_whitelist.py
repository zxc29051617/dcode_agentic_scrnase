"""The FASTQ route must not depend on a Cell Ranger install being present.

CI found this the first time it ran, and a developer with Cell Ranger installed
never would have. `tests/fixtures.py` read the real barcode whitelist out of
whatever Cell Ranger it could glob in the home directory, and *silently fell
back to random ACGT* when there wasn't one. Random ACGT is correctly reported as
"not 10x data", so `fastq_preflight` warned, the run stopped at the gate, and
two graph tests failed — for a reason that had nothing to do with the code they
were testing.

The fix is a whitelist the repository owns, passed to the step explicitly. What
is pinned here is that it stays that way: the fixture never degrades quietly,
the tests never consult the machine, and production still looks where Cell
Ranger actually puts things.

Run with `python tests/test_fastq_whitelist.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph import build_graph  # noqa: E402
from src.judge import StubJudge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.registry import REGISTRY, call_skill  # noqa: E402
from src.run import DEFAULT_RECURSION_LIMIT  # noqa: E402
from src.state import new_run_state  # noqa: E402
from tests import fixtures  # noqa: E402

WHITELIST_DIR = fixtures.SYNTHETIC_WHITELIST_DIR

#: A path that cannot exist, so "Cell Ranger is not installed" is a property of
#: the test rather than of the machine running it.
ABSENT_BINARY = "/nonexistent/cellranger-never-installed"


def _preflight(bundle: Path, reference: Path, **config):
    return call_skill("fastq_preflight", {
        "step": "fastq_preflight",
        "run_id": "t",
        "run_dir": str(bundle.parent),
        "config": {"reference": str(reference), **config},
        "input_bundle": {"paths": [str(bundle)]},
        "artifacts": {},
        "sample_metadata": {},
    })


# --- the whitelist the repository owns -------------------------------------------------


class Skip(Exception):
    """Raised by a test that needs a tool this machine does not have."""


def _need_fastqc() -> None:
    """The FASTQ route stops at a gate without it, so these tests cannot run.

    `fastq_qc` treats a missing FastQC as advisory and returns `ok` with a
    warning — but under the default `GatePolicy` a warning routes to the human
    gate, and with nobody there to answer the run halts before
    `cellranger_count`. So on a machine without FastQC these tests never reach
    what they are about, and they failed for a reason that had nothing to do
    with the thing under test.

    `fastqc=0.12.1` is in `environment.yml`, so an environment built from the
    lockfile has it and runs these in full. This only skips where it is absent.
    Guarded the same way `tests/test_fastq_qc.py` guards its own.
    """
    import shutil

    if shutil.which("fastqc") is None:
        raise Skip("fastqc is not installed; the FASTQ route halts at the QC gate")


def test_the_synthetic_whitelist_is_committed_and_named_for_a_chemistry():
    """The filename is load-bearing: chemistry is looked up by it."""
    assert fixtures.SYNTHETIC_WHITELIST.exists(), "the fixtures cannot work without it"
    assert fixtures.SYNTHETIC_WHITELIST.name == "737K-august-2016.txt"

    from importlib import import_module
    preflight = import_module("dcode_scrna_skills.fastq_preflight")
    known = {name for name, _ in preflight.WHITELIST_CHEMISTRY}
    assert fixtures.SYNTHETIC_WHITELIST.name in known, (
        "a whitelist with an unrecognised name is read and then matched to nothing"
    )


def test_every_barcode_the_fixture_writes_is_on_that_whitelist():
    """They only work as a pair; regenerating one without the other is silent."""
    import gzip

    listed = set(fixtures.synthetic_barcodes())
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_10x_fastq_trio(Path(tmp), n_reads=64)
        r1 = next(bundle.glob("*_R1_*.fastq.gz"))
        with gzip.open(r1, "rt") as handle:
            written = {
                line.strip()[:16]
                for index, line in enumerate(handle) if index % 4 == 1
            }

    assert written, "the fixture wrote no reads"
    assert written <= listed, f"{len(written - listed)} barcodes are not on the whitelist"


def test_the_fixture_refuses_rather_than_quietly_writing_random_barcodes():
    """The exact failure that reached CI: a promise silently downgraded."""
    original = fixtures.SYNTHETIC_WHITELIST
    fixtures.SYNTHETIC_WHITELIST = Path("/nonexistent/whitelist.txt")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures.make_10x_fastq_trio(Path(tmp))
    except FileNotFoundError as exc:
        assert "committed to this repository" in str(exc)
    else:
        raise AssertionError("a fixture that cannot keep its promise must say so")
    finally:
        fixtures.SYNTHETIC_WHITELIST = original


# --- preflight, with no Cell Ranger anywhere -------------------------------------------


def test_preflight_identifies_the_chemistry_from_the_supplied_whitelist():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        bundle = fixtures.make_10x_fastq_trio(root / "bundle", n_reads=200)
        result = _preflight(bundle, reference, barcode_whitelist_dir=str(WHITELIST_DIR))

    assert result["status"] == "ok", result["errors"]
    assert result["errors"] == []
    assert result["warnings"] == [], "an identified chemistry leaves nothing to warn about"

    library = result["output"]["detected_libraries"][0]
    assert "SC3Pv2" in library["chemistry_guess"]
    evidence = library["chemistry_evidence"]
    assert evidence["matched_whitelist"] == "737K-august-2016.txt"
    assert evidence["whitelist_hit_rate"]["737K-august-2016.txt"] == 1.0, (
        "every barcode came from this list, so anything below 1.0 means it read another"
    )


def test_without_the_whitelist_the_same_bundle_is_only_a_warning():
    """The behaviour is unchanged; it is the *tests* that stopped depending on it.

    Pointing at an empty directory stands in for a machine with no Cell Ranger.
    Preflight still succeeds — an unidentifiable chemistry has never been fatal —
    but it warns, and that warning is what used to stop the run at a gate.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        empty = root / "no-whitelists"
        empty.mkdir()
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        bundle = fixtures.make_10x_fastq_trio(root / "bundle", n_reads=200)
        result = _preflight(bundle, reference, barcode_whitelist_dir=str(empty))

    assert result["status"] == "ok"
    library = result["output"]["detected_libraries"][0]
    assert library["chemistry_guess"] == []
    assert any("chemistry" in w.lower() or "whitelist" in w.lower()
               for w in result["warnings"]), result["warnings"]


def test_the_whitelist_directory_is_declared_in_the_registry():
    """`--resume-from` has to know that changing it invalidates the step."""
    assert "barcode_whitelist_dir" in REGISTRY["fastq_preflight"].config_keys


# --- the route, end to end, on a machine with no Cell Ranger ----------------------------


def _run_fastq_route(root: Path, *, policy: GatePolicy | None = None, **config):
    reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
    bundle = fixtures.make_10x_fastq_trio(root / "bundle", n_reads=200)
    graph = build_graph(
        policy=policy or GatePolicy(headless_decision="accept"), judge=StubJudge()
    )
    state = new_run_state(
        project="test",
        config={
            "species": "human",
            "transcriptome": str(reference),
            "barcode_whitelist_dir": str(WHITELIST_DIR),
            **config,
        },
        input_bundle={"paths": [str(bundle)]},
        runs_dir=root / "runs",
    )
    return graph.invoke(state, config={"recursion_limit": DEFAULT_RECURSION_LIMIT})


def test_the_synthetic_bundle_passes_preflight_and_reaches_the_count():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run_fastq_route(Path(tmp), binary=ABSENT_BINARY)

    verdict = next(j for j in final["judge_results"] if j["step"] == "fastq_preflight")
    assert verdict["verdict"] == "pass", verdict["reasons"]
    steps = [r["step"] for r in final["step_results"]]
    assert "cellranger_count" in steps, "preflight must not be what stops this run"


def test_a_binary_that_cannot_exist_halts_without_a_report():
    """Same outcome whether or not this machine has Cell Ranger installed.

    On the default policy, which does not wave a `fail` through — under
    `headless_decision="accept"` the operator has said to carry on past a failed
    count, and carrying on is then the correct behaviour rather than a bug.
    """
    _need_fastqc()
    with tempfile.TemporaryDirectory() as tmp:
        final = _run_fastq_route(Path(tmp), policy=GatePolicy(), binary=ABSENT_BINARY)

    steps = [r["step"] for r in final["step_results"]]
    assert "cellranger_count" in steps
    assert "build_report" not in steps
    assert "run_qc_metrics" not in steps
    assert final["halted"] is True
    assert any("cellranger" in error for error in final["errors"])
    assert any(ABSENT_BINARY in error for error in final["errors"]), (
        "the error has to name the binary that was asked for"
    )


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures, skipped = [], 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except Skip as reason:
            skipped += 1
            print(f"  skip  {test.__name__}: {reason}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    passed = len(tests) - len(failures) - skipped
    print(f"\n{passed}/{len(tests) - skipped} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

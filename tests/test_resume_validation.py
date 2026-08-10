"""Resuming after something changed, through the real pipeline.

`plan_resume` is unit-tested in `test_persistence.py`. What is pinned here is
the part that only shows up end to end: that the cut lands where the science
says it should, on a run that actually produced the artifacts being reasoned
about.

The three cases are the three things that move a cut, and they are deliberately
at different depths:

  - a QC threshold changes    -> cut at `apply_cell_qc_filter`, QC metrics kept
  - the CellTypist model changes -> cut at `annotate_cells`, the whole embedding
    and marker analysis kept, which the old whole-directory hash threw away
  - the input matrix changes  -> cut at the first step, nothing kept

Run with `python tests/test_resume_validation.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.policy import GatePolicy  # noqa: E402
from src.provenance import AuditLog  # noqa: E402
from src.registry import REGISTRY  # noqa: E402
from src.run import run_workflow  # noqa: E402
from src.state import summarize  # noqa: E402
from tests import fixtures  # noqa: E402

#: Permissive on purpose. These tests are about what a resume reuses, not about
#: what a threshold should be; the fixture carries a handful of genes per cell,
#: so a realistic `min_genes` would empty it.
BASE = {"min_genes": 1, "max_pct_mito": 100}

WALK = GatePolicy(headless_decision="accept")


def _setup(root: Path) -> tuple[dict, dict, Path]:
    matrix = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"}, root / "b")
    reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
    bundle = {"paths": [str(matrix)]}
    config = {"species": "human", "transcriptome": str(reference), **BASE}
    return bundle, config, matrix


def _run(root: Path, bundle: dict, config: dict, *, resume: str | None = None):
    return run_workflow(
        project="test", input_bundle=bundle, config=config,
        runs_dir=str(root / "runs"), policy=WALK, resume_run_id=resume,
    )


def _skipped(final) -> set[str]:
    return set(summarize(final)["skipped"])


def _ran(final) -> set[str]:
    return {r["step"] for r in final["step_results"] if r["status"] != "skipped"}


def _plan_event(final) -> dict:
    events = AuditLog(final["audit_log_path"]).read()
    return next(e for e in reversed(events) if e["event"] == "resume_plan")


def _resume(root: Path, bundle: dict, config: dict, run_id: str) -> tuple[dict, dict]:
    """Resume, and read the plan the run recorded before the directory goes away.

    The audit log lives in the temporary directory, so the plan has to be picked
    up while the run is still on disk — reading it after the `with` block sees an
    empty log and quietly finds nothing.
    """
    final = _run(root, bundle, config, resume=run_id)
    return final, _plan_event(final)


# --- 1. a QC threshold changes ------------------------------------------------------


def test_changing_a_qc_threshold_reruns_from_the_filter_and_keeps_the_qc_metrics():
    """`run_qc_metrics` does not read `min_genes`, so it survives the change."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        assert "build_report" in _ran(first), "precondition: the first run completed"

        second, plan = _resume(root, bundle, {**config, "min_genes": 2}, first["run_id"])

    skipped, ran = _skipped(second), _ran(second)
    assert "run_qc_metrics" in skipped, "QC metrics do not depend on the threshold"
    assert "post_load_validate" in skipped
    assert "apply_cell_qc_filter" in ran, "the step that reads it has to run again"
    for downstream in ("detect_doublets", "run_pca", "run_clustering", "annotate_cells"):
        assert downstream in ran, f"{downstream} was computed from the old filter"
        assert downstream not in skipped

    assert plan["rerun_from"] == "apply_cell_qc_filter"
    assert any("min_genes" in reason for reason in plan["reasons"])


# --- 2. the annotation model changes -------------------------------------------------


def test_changing_the_celltypist_model_keeps_the_clustering_and_the_markers():
    """The case the whole-directory hash got wrong, and the reason for this change.

    Nothing between loading and `find_markers` reads `celltypist_model`. Under
    the old rule a changed hash threw the entire directory away and recomputed a
    PCA, a Harmony correction, a Leiden clustering, a UMAP and a Wilcoxon test
    over every gene — none of which could have come out differently.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        assert "find_markers" in _ran(first), "precondition"

        second, plan = _resume(
            root, bundle, {**config, "celltypist_model": "Immune_All_Low.pkl"},
            first["run_id"],
        )

    skipped, ran = _skipped(second), _ran(second)
    for kept in ("run_qc_metrics", "apply_cell_qc_filter", "detect_doublets",
                 "normalize_hvg_prepare", "run_pca", "run_integration",
                 "run_clustering", "run_umap", "find_markers"):
        assert kept in skipped, f"{kept} cannot be affected by the model choice"
    assert "annotate_cells" in ran
    assert "cross_check_annotation" in ran, "it reads the labels annotate_cells wrote"

    assert plan["rerun_from"] == "annotate_cells"


def test_changing_the_cross_check_tissue_keeps_the_annotation_itself():
    """The cut can land on the very last analysis step, and does."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        second, plan = _resume(root, bundle, {**config, "scmayomap_tissue": "blood"},
                               first["run_id"])

    skipped, ran = _skipped(second), _ran(second)
    assert "annotate_cells" in skipped, "CellTypist does not read the marker-database tissue"
    assert "find_markers" in skipped
    assert "cross_check_annotation" in ran
    assert plan["rerun_from"] == "cross_check_annotation"


# --- 3. the input data changes --------------------------------------------------------


def test_changing_the_input_matrix_reruns_everything():
    """Every step read from it, directly or through the object it produced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, matrix = _setup(root)
        first = _run(root, bundle, config)
        assert "build_report" in _ran(first), "precondition"

        # A different matrix at the same path: the case a size-and-mtime check
        # is allowed to miss and a content hash is not.
        fixtures.make_mtx_dir(root / "b", "filtered_feature_bc_matrix", n_barcodes=800)

        second, plan = _resume(root, bundle, config, first["run_id"])

    assert _skipped(second) == set(), "nothing computed from the old matrix may survive"
    assert plan["reused"] == []
    assert plan["rerun_from"] == "ingest_validate"
    assert any("input data changed" in reason for reason in plan["reasons"])


def test_an_untouched_input_is_not_mistaken_for_a_changed_one():
    """The digest reads the same files twice and has to get the same answer."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        second, plan = _resume(root, bundle, config, first["run_id"])

    assert _skipped(second), "an untouched input has to still be resumable"
    assert plan["rerun_from"] is None
    assert not any("input data" in reason for reason in plan["reasons"])


def test_recompressing_an_input_reruns_it_and_that_is_the_safe_direction():
    """A `.gz` rewritten with identical content is not identical on disk.

    gzip stores its own modification time in the header, so re-compressing the
    same matrix produces different bytes — measured, not assumed. The digest
    hashes the file as stored, so this reads as a data change and the run
    recomputes.

    Pinned rather than fixed. Comparing what is *inside* each file would mean a
    decompressor per format, and the failure this guard exists to prevent is the
    other one: reusing an analysis after its input really did change.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)

        # Same fixture, same parameters, same contents — recompressed.
        fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"}, root / "b")

        second, plan = _resume(root, bundle, config, first["run_id"])

    assert _skipped(second) == set()
    assert plan["rerun_from"] == "ingest_validate"


# --- the guard that must not be quiet --------------------------------------------------


def test_a_deleted_artifact_reruns_that_step_and_everything_after_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        (root / "runs" / first["run_id"] / "run_pca" / "adata.h5ad").unlink()

        second, plan = _resume(root, bundle, config, first["run_id"])

    skipped, ran = _skipped(second), _ran(second)
    assert "normalize_hvg_prepare" in skipped, "it ran before the missing object"
    assert "run_pca" in ran
    assert "run_clustering" in ran, "it was computed from the object that is gone"
    assert plan["rerun_from"] == "run_pca"


def test_an_unchanged_resume_still_skips_what_it_already_did():
    """The plain case has to keep working, or the whole thing is just a slow rerun."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config, _ = _setup(root)
        first = _run(root, bundle, config)
        second = _run(root, bundle, config, resume=first["run_id"])

    completed = {r["step"] for r in first["step_results"] if r["status"] == "ok"}
    skipped = _skipped(second)
    assert skipped, "an unchanged resume reuses work"
    assert skipped <= completed
    assert second["errors"] == []


# --- the map the cut is computed from -------------------------------------------------


#: Read from a skill's payload but not a setting the pipeline varies: plumbing
#: for the standalone CLIs, and keys a step is handed rather than configured
#: with. Listing them is cheaper than a rule that tries to tell them apart.
_NOT_SETTINGS = {"adata_path", "adata_paths"}


def test_no_skill_reads_a_config_key_its_step_does_not_declare():
    """The one direction of drift that is a correctness bug rather than a cost.

    A key listed on a step that does not read it forces a needless rerun. A key
    read by a step that does not list it is the opposite: the cut lands after
    that step, and a resume hands back a result computed from a value the
    operator has since changed.

    So this re-derives the second half from the source on every run instead of
    trusting the registry to have been kept in step by hand. Static, no imports
    — a grep the test suite performs rather than a person.
    """
    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(r"""config(?:\.get\(|\[)["']([a-z_0-9]+)["']""")

    missing: list[str] = []
    for step, spec in REGISTRY.items():
        source = root / "skills" / step / f"{step}.py"
        if not source.exists():
            continue
        text = source.read_text(encoding="utf-8")
        for key in sorted(set(pattern.findall(text))):
            if key in _NOT_SETTINGS or key in spec.config_keys:
                continue
            missing.append(f"{step} reads config[{key!r}] but does not declare it")
    assert not missing, "\n".join(missing)


def test_every_revisable_parameter_is_also_a_config_key_of_its_step():
    """A gate can set it, so changing it has to be able to invalidate the step."""
    for step, spec in REGISTRY.items():
        for key in spec.revisable:
            assert key in spec.config_keys, f"{step}.{key} is revisable but not a config_key"


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

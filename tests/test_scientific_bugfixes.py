"""Three bugs with a right answer, and the behaviour that replaced them.

Each was silent: none of them raised, and each produced a plausible-looking
result. That is what makes them worth a test file of their own rather than a
line in an existing one.

  1. `median_conf_score` of 0.0 — the *least* confident annotation possible —
     is falsy, so `(entry.get(...) or 1.0)` read it as 1.0 and the final gate
     never mentioned the clusters most in need of a person's eye.
  2. Two cells and `method="tsne"` produced `perplexity=2`, which violates
     sklearn's `0 < perplexity < n_obs`; on `method="both"` a UMAP was computed
     and stored first, so the failure arrived after a partial result.
  3. Every sample was compared against the first Cell Ranger library that could
     be read. 10x barcodes come from a fixed whitelist, so comparing sample A to
     library B's calls produced an overlap that looked like a measurement.

Everything here is synthetic: no public dataset, no reference, no Cell Ranger.

Run with `python tests/test_scientific_bugfixes.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import call_skill, load_skill  # noqa: E402

hrd = load_skill("human_review_decision")
umap_skill = load_skill("run_umap")
ccr = load_skill("cell_calling_review")


# --- 1. median_conf_score ---------------------------------------------------------------


def _confidence_concerns(entry) -> list[str]:
    """The concerns `human_review_decision` raises for one cluster."""
    art = {"annotate_cells": {"per_cluster": {"c1": entry}}}
    return [c for c in hrd._open_concerns(art) if "confidence" in c.lower()]


def test_a_median_confidence_of_zero_is_the_lowest_score_not_a_missing_one():
    """`0.0 or 1.0` is 1.0, and 0.0 is exactly the case that needs a person."""
    value, problem = hrd._median_confidence({"median_conf_score": 0.0})
    assert problem is None
    assert value == 0.0
    assert value < hrd.LOW_CONFIDENCE_MEDIAN
    assert _confidence_concerns({"median_conf_score": 0.0}), "0.0 must reach the gate"


def test_the_existing_low_confidence_behaviour_is_unchanged():
    assert _confidence_concerns({"median_conf_score": 0.2})


def test_the_threshold_is_strictly_below_as_annotate_cells_compares_it():
    """`annotate_cells` does `median_conf < LOW_CONFIDENCE_MEDIAN`; so does this."""
    assert hrd.LOW_CONFIDENCE_MEDIAN == 0.5
    assert not _confidence_concerns({"median_conf_score": 0.5}), "0.5 is not below 0.5"
    assert _confidence_concerns({"median_conf_score": 0.499})
    assert not _confidence_concerns({"median_conf_score": 0.6})


def test_a_missing_or_null_score_is_not_treated_as_confident():
    for entry, label in [({}, "absent"), ({"median_conf_score": None}, "null")]:
        value, problem = hrd._median_confidence(entry)
        assert value is None and problem, label
        assert _confidence_concerns(entry), f"a {label} score must raise a concern"


def test_a_non_numeric_or_non_finite_score_gets_a_named_concern():
    for raw, label in [("high", "string"), (float("nan"), "NaN"),
                       (float("inf"), "infinity"), (True, "bool")]:
        entry = {"median_conf_score": raw}
        value, problem = hrd._median_confidence(entry)
        assert value is None, f"{label} must not be scored"
        assert problem, label
        concerns = _confidence_concerns(entry)
        assert concerns, f"{label} must raise a concern"
        assert any("unknown" in c for c in concerns), label


def test_the_recorded_value_is_never_repaired():
    """It reports that it cannot use the number; it does not invent one."""
    entry = {"median_conf_score": "not a number"}
    hrd._median_confidence(entry)
    assert entry == {"median_conf_score": "not a number"}


# --- 2. t-SNE perplexity -----------------------------------------------------------------


def _tiny_adata(n_obs: int, path: Path) -> str:
    import anndata as ad
    import numpy as np

    adata = ad.AnnData(np.ones((n_obs, 4), dtype="float32"))
    adata.obs_names = [f"c{i}" for i in range(n_obs)]
    adata.obsm["X_pca"] = np.random.default_rng(0).normal(size=(n_obs, 3))
    # A neighbour graph has to be present or the `umap`/`both` branch stops at its
    # own "run_clustering must run first" guard — which would let the partial-UMAP
    # test below pass for a reason that has nothing to do with perplexity.
    adata.uns["neighbors"] = {
        "connectivities_key": "connectivities",
        "distances_key": "distances",
        "params": {"n_neighbors": 2, "method": "umap"},
    }
    adata.write_h5ad(path)
    return str(path)


def _run_umap(source: str, run_dir: Path, **config) -> dict:
    return call_skill("run_umap", {
        "step": "run_umap", "run_id": "t", "run_dir": str(run_dir),
        "config": {"adata_path": source, **config},
        "input_bundle": {}, "artifacts": {}, "sample_metadata": {},
    })


def test_two_cells_and_tsne_is_refused_before_anything_is_embedded():
    import scanpy as sc

    called: list[str] = []
    original_tsne, original_umap = sc.tl.tsne, sc.tl.umap
    sc.tl.tsne = lambda *a, **k: called.append("tsne")
    sc.tl.umap = lambda *a, **k: called.append("umap")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _tiny_adata(2, root / "a.h5ad")
            result = _run_umap(source, root, method="tsne")
    finally:
        sc.tl.tsne, sc.tl.umap = original_tsne, original_umap

    assert result["status"] == "ok"
    assert result["errors"], "two cells cannot produce a legal perplexity"
    message = " ".join(result["errors"])
    assert "at least 3 cells" in message and "2" in message
    assert called == [], f"nothing may be embedded first, but {called} ran"


def test_two_cells_and_both_does_not_compute_a_umap_first():
    """The partial-result case: `both` used to store a UMAP and then fail."""
    import scanpy as sc

    called: list[str] = []
    original_tsne, original_umap = sc.tl.tsne, sc.tl.umap
    sc.tl.tsne = lambda *a, **k: called.append("tsne")
    sc.tl.umap = lambda *a, **k: called.append("umap")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _tiny_adata(2, root / "a.h5ad")
            result = _run_umap(source, root, method="both")
    finally:
        sc.tl.tsne, sc.tl.umap = original_tsne, original_umap

    assert result["errors"], "two cells cannot produce a legal perplexity"
    assert called == [], f"no embedding may run first, but {called} did"


def test_three_cells_passes_a_legal_perplexity_to_the_call():
    seen: dict[str, float] = {}
    import scanpy as sc

    original = sc.tl.tsne

    def spy(adata, **kwargs):
        seen["perplexity"] = float(kwargs.get("perplexity"))
        seen["n_obs"] = int(adata.n_obs)
        import numpy as np
        adata.obsm["X_tsne"] = np.zeros((adata.n_obs, 2))

    sc.tl.tsne = spy
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _tiny_adata(3, root / "a.h5ad")
            _run_umap(source, root, method="tsne")
    finally:
        sc.tl.tsne = original

    assert seen, "t-SNE was never called"
    assert 0 < seen["perplexity"] < seen["n_obs"], seen


def test_an_over_large_perplexity_is_clamped_below_the_cell_count():
    import scanpy as sc

    seen: dict[str, float] = {}
    original = sc.tl.tsne

    def spy(adata, **kwargs):
        seen["perplexity"] = float(kwargs.get("perplexity"))
        seen["n_obs"] = int(adata.n_obs)
        import numpy as np
        adata.obsm["X_tsne"] = np.zeros((adata.n_obs, 2))

    sc.tl.tsne = spy
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _tiny_adata(10, root / "a.h5ad")
            result = _run_umap(source, root, method="tsne", perplexity=500)
    finally:
        sc.tl.tsne = original

    assert 0 < seen["perplexity"] < seen["n_obs"], seen
    assert any("perplexity" in w for w in result["warnings"]), "a clamp has to be said out loud"


def test_a_perplexity_that_is_not_a_finite_positive_number_fails_closed():
    for raw, label in [(0, "zero"), (-5, "negative"), (float("nan"), "NaN"),
                       (float("inf"), "infinity"), ("wide", "string")]:
        value, problem = umap_skill._requested_perplexity({"perplexity": raw})
        assert problem, f"{label} must be refused"
        assert value == float(umap_skill.DEFAULT_PERPLEXITY), (
            f"{label} must not become the effective value"
        )


def test_a_bad_perplexity_stops_the_step_rather_than_reaching_sklearn():
    import scanpy as sc

    called: list[str] = []
    original = sc.tl.tsne
    sc.tl.tsne = lambda *a, **k: called.append("tsne")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _tiny_adata(10, root / "a.h5ad")
            result = _run_umap(source, root, method="tsne", perplexity=float("nan"))
    finally:
        sc.tl.tsne = original

    assert result["errors"], "a NaN perplexity has to be refused"
    assert called == [], "and never handed to sklearn"


# --- 3. per-library barcode comparison ------------------------------------------------------


def _matrix(path: Path, barcodes: list[str]) -> str:
    import h5py
    import numpy as np

    with h5py.File(path, "w") as handle:
        group = handle.create_group("matrix")
        group.create_dataset("barcodes", data=np.array([b.encode() for b in barcodes]))
    return str(path)


#: Deliberately overlapping: real 10x barcodes come from one whitelist, so two
#: libraries sharing strings is normal and is exactly what made the old bug look
#: like a plausible comparison rather than an obvious mix-up.
A_CALLED = ["AAACCCAAGAAACACT-1", "AAACCCAAGAAACCAT-1", "AAACCCAAGAAACCCA-1"]
B_CALLED = ["AAACCCAAGAAACCCA-1", "TTTGTTGGTTTGGGTA-1"]


def _libraries(root: Path, order: tuple[str, ...]) -> dict:
    paths = {"A": _matrix(root / "A.h5", A_CALLED), "B": _matrix(root / "B.h5", B_CALLED)}
    return {"cellranger_count": {"libraries": [
        {"library_id": name, "filtered_feature_bc_matrix": paths[name]} for name in order
    ]}}


def test_each_sample_is_matched_to_its_own_library():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Listed B first on purpose: position must not decide anything.
        by_library, problems = ccr._cellranger_called_by_library(_libraries(root, ("B", "A")))

    assert problems == [], problems
    assert by_library["A"] == set(A_CALLED)
    assert by_library["B"] == set(B_CALLED)
    assert by_library["A"] != by_library["B"], "the fixture must actually distinguish them"


def test_library_order_does_not_change_the_result():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        forward, _ = ccr._cellranger_called_by_library(_libraries(root, ("A", "B")))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reversed_, _ = ccr._cellranger_called_by_library(_libraries(root, ("B", "A")))

    assert forward == reversed_


def test_a_sample_with_no_matching_library_gets_no_comparison():
    """It must not borrow another sample's cell calls."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        by_library, _ = ccr._cellranger_called_by_library(_libraries(root, ("A",)))

    called, problem = ccr._called_for_sample("B", by_library, n_samples=2)
    assert called is None, "B must not be handed A's barcodes"
    assert problem and "B" in problem and "A" in problem, problem


def test_a_duplicated_library_id_is_refused_rather_than_picked():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _matrix(root / "one.h5", A_CALLED)
        second = _matrix(root / "two.h5", B_CALLED)
        artifacts = {"cellranger_count": {"libraries": [
            {"library_id": "A", "filtered_feature_bc_matrix": first},
            {"library_id": "A", "filtered_feature_bc_matrix": second},
        ]}}
        by_library, problems = ccr._cellranger_called_by_library(artifacts)

    assert "A" not in by_library, "with two claims to one id, neither is evidence"
    assert any("ambiguous" in p and "A" in p for p in problems), problems


def test_an_unreadable_matrix_is_named_rather_than_skipped_silently():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        broken = root / "B.h5"
        broken.write_bytes(b"not an hdf5 file")
        artifacts = {"cellranger_count": {"libraries": [
            {"library_id": "A", "filtered_feature_bc_matrix": _matrix(root / "A.h5", A_CALLED)},
            {"library_id": "B", "filtered_feature_bc_matrix": str(broken)},
        ]}}
        by_library, problems = ccr._cellranger_called_by_library(artifacts)

    assert set(by_library) == {"A"}
    assert any("B" in p for p in problems), problems


def test_one_sample_and_one_library_still_compare():
    """The standalone case, where there is nothing to confuse them with."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        by_library, _ = ccr._cellranger_called_by_library(_libraries(root, ("A",)))

    called, problem = ccr._called_for_sample("some_other_name", by_library, n_samples=1)
    assert called == set(A_CALLED), "one sample, one library, no ambiguity"
    assert problem is None


def test_the_single_library_fallback_does_not_apply_to_two_samples():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        by_library, _ = ccr._cellranger_called_by_library(_libraries(root, ("A",)))

    called, problem = ccr._called_for_sample("B", by_library, n_samples=2)
    assert called is None and problem


def test_no_libraries_at_all_is_silent_rather_than_a_warning_per_sample():
    called, problem = ccr._called_for_sample("A", {}, n_samples=2)
    assert called is None
    assert problem is None, "a run with no Cell Ranger output has nothing to complain about"


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
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, not a stop
            failures.append(test.__name__)
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

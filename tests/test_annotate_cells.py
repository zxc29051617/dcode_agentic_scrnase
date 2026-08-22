"""Tests for `annotate_cells`.

Annotation needs a CellTypist model, which is a download rather than something
every checkout has. Tests that need one skip cleanly when it is absent, the
same way the FASTQ suites skip without their reference — the refusal path and
the failure modes are checked regardless, since those are what this step is
mostly for.

Run with `python tests/test_annotate_cells.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

ac = load_skill("annotate_cells")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _cached_model() -> str:
    """A locally downloaded CellTypist model, or skip."""
    try:
        from celltypist import models

        downloaded = list(models.get_all_models())
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"celltypist unavailable: {exc}") from exc
    if not downloaded:
        raise Skip("no CellTypist model downloaded on this machine")
    return downloaded[0]


def _adata(n_cells=90, n_genes=80, *, counts_layer=True, clusters=True):
    """Log-normalized expression with a counts layer, clusters, and a UMAP."""
    import anndata
    import numpy as np
    import pandas as pd
    import scanpy as sc
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    counts = rng.poisson(3, size=(n_cells, n_genes)).astype("float32")

    adata = anndata.AnnData(sp.csr_matrix(counts))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    # Real gene symbols, so a model has something to match against.
    adata.var_names = (
        ["CD3D", "CD8A", "MS4A1", "CD79A", "NKG7", "GNLY", "LYZ", "S100A8", "FCGR3A", "PPBP"]
        + [f"GENE{i}" for i in range(n_genes - 10)]
    )[:n_genes]
    if counts_layer:
        adata.layers["counts"] = adata.X.copy()
    if clusters:
        adata.obs["leiden"] = pd.Categorical([str(i % 3) for i in range(n_cells)])
    sc.pp.normalize_total(adata)  # median depth, deliberately not 10,000
    sc.pp.log1p(adata)
    adata.obsm["X_umap"] = rng.normal(size=(n_cells, 2)).astype("float32")
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return ac.run(
        {
            "artifacts": {"find_markers": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the model is a decision, not a default ----------------------------------


def test_no_model_annotates_nothing_and_reports_the_candidates():
    """A wrong-tissue model returns confident wrong labels, so it is not guessed."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata())
    assert result["errors"] == []
    assert result["annotation_state"] == "needs_review"
    assert result["adata_path"] is None, "nothing may be written without a model choice"
    assert any("no celltypist_model chosen" in w for w in result["warnings"])


def test_the_candidate_list_is_evidence_an_advisor_can_read():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata())
    models = result["evidence"]["models"]
    assert "downloaded" in models
    if "available" in models:
        assert models["available"], "the catalogue should not be empty when reachable"
        assert {"model", "description"} <= set(models["available"][0])


# --- picking a model you actually have -----------------------------------------------------
#
# The failure this closes, in full: on 2026-08-22 a model was chosen from
# `--list-models`, the FASTQ route ran for half an hour, and step 22 of 26 died
# on `FileNotFoundError: No such file: Developing_Mouse_Brain.pkl`. The
# catalogue lists sixty-one models; three were on the machine. Both numbers
# were already in the payload, in two separate lists, and joining them was left
# to the reader. `apps/web/lib/gateCandidates.ts` had done that join for the
# browser for some time; the terminal had not.


def test_every_catalogue_row_says_whether_it_is_here():
    catalogue = ac._model_catalogue()
    if "available" not in catalogue:
        raise Skip("the published catalogue is not reachable")
    downloaded = set(catalogue.get("downloaded") or [])
    for row in catalogue["available"]:
        assert "local" in row, f"{row['model']} does not say whether it is downloaded"
        assert row["local"] == (row["model"] in downloaded), row["model"]


def test_a_model_that_is_not_downloaded_is_named_as_such():
    # Injected rather than measured: which models a machine holds is exactly
    # the thing that differs between machines, so a test that asserted a real
    # one would pass here and fail on a fresh checkout.
    hint = ac._absent_model_hint(
        "Developing_Mouse_Brain.pkl",
        {"downloaded": ["Immune_All_Low.pkl", "Immune_All_High.pkl"]},
    )
    assert "not downloaded" in hint
    assert "download_models" in hint, "the hint has to say how to get it"
    assert "Immune_All_Low.pkl" in hint, "and what is available instead"
    assert "--resume-from" in hint, "resuming beats re-running twenty-one steps"


def test_a_downloaded_model_gets_no_download_hint():
    # Then the failure is something else, and a download instruction would send
    # somebody after the wrong problem.
    hint = ac._absent_model_hint(
        "Immune_All_Low.pkl", {"downloaded": ["Immune_All_Low.pkl"]}
    )
    assert hint == ""


def test_an_empty_cache_is_reported_as_none_rather_than_blank():
    hint = ac._absent_model_hint("Whatever.pkl", {"downloaded": []})
    assert "none" in hint


def test_the_listing_groups_what_is_here_apart_from_what_is_not():
    catalogue = {
        "downloaded": ["A.pkl"],
        "available": [
            {"model": "A.pkl", "description": "here", "local": True},
            {"model": "B.pkl", "description": "absent", "local": False},
            {"model": "C.pkl", "description": "absent", "local": False},
        ],
    }
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        ac._print_catalogue(catalogue)
    printed = buffer.getvalue()
    assert "Downloaded, ready to use (1)" in printed
    assert "Not downloaded (2)" in printed
    # The one that is here must come first: a reader scanning from the top
    # should reach a usable answer before a list of things to wait for.
    assert printed.index("A.pkl") < printed.index("B.pkl")


def test_an_unreachable_catalogue_still_lists_the_cache():
    # Offline is not "no models". The cache needs no network and is the only
    # set that works without one.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        ac._print_catalogue(
            {"downloaded": ["A.pkl"], "available_error": "ConnectionError: nope"}
        )
    printed = buffer.getvalue()
    assert "A.pkl" in printed and "downloaded" in printed


# --- annotating ----------------------------------------------------------------


def test_annotation_writes_labels_and_confidence():
    model = _cached_model()
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(), celltypist_model=model)
        assert result["errors"] == [], result["errors"]
        assert result["annotation_state"] == "annotated"
        written = anndata.read_h5ad(result["adata_path"])
        assert {"cell_type", "cell_type_per_cell", "conf_score"} <= set(written.obs)
        assert written.obs["conf_score"].between(0, 1).all()


def test_expression_is_rebuilt_at_ten_thousand_not_reused_from_x():
    """CellTypist degrades quietly on anything else, and X here is median-normalized."""
    model = _cached_model()
    import anndata
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = _adata()
        before = float(np.expm1(source.X[0].toarray()).sum())
        result = _run(root, source, celltypist_model=model)
        assert result["annotation_summary"]["normalized_to"] == 10000.0
        assert abs(before - 10000.0) > 1, "the fixture must not already be at 10,000"
        # The mainline X is left exactly as it was.
        written = anndata.read_h5ad(result["adata_path"])
        assert abs(float(np.expm1(written.X[0].toarray()).sum()) - before) < 1.0


def test_majority_voting_runs_over_our_clusters():
    model = _cached_model()
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), celltypist_model=model)
    assert result["annotation_summary"]["over_clustering"] == "leiden"
    assert result["annotation_summary"]["label_source"] == "majority_voting"


def test_per_cluster_reports_confidence_and_consensus():
    model = _cached_model()
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), celltypist_model=model)
    assert set(result["per_cluster"]) == {"0", "1", "2"}
    entry = result["per_cluster"]["0"]
    assert {"n_cells", "cell_type", "median_conf_score", "per_cell_consensus"} <= set(entry)
    assert 0.0 <= entry["per_cell_consensus"] <= 1.0


def test_a_figure_is_written_for_each_embedding():
    model = _cached_model()
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), celltypist_model=model)
        assert "umap" in result["figure_paths"], result["notes"]
        assert Path(result["figure_paths"]["umap"]).exists()
        assert Path(result["figure_paths"]["umap"]).stat().st_size > 0


# --- failures ------------------------------------------------------------------


def test_missing_path_is_an_error():
    result = ac.run({"artifacts": {}, "run_dir": "."})
    assert any("find_markers must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = ac.run({"artifacts": {"find_markers": {"adata_path": "/nope.h5ad"}}, "run_dir": "."})
    assert any("does not exist" in e for e in result["errors"])


def test_missing_counts_layer_is_an_error():
    model = _cached_model()
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(counts_layer=False), celltypist_model=model)
    assert any("post_load_validate must run first" in e for e in result["errors"])


def test_missing_cluster_labels_is_an_error():
    model = _cached_model()
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(clusters=False), celltypist_model=model)
    assert any("run_clustering must run first" in e for e in result["errors"])


def test_an_unknown_model_is_an_error_with_the_catalogue_attached():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), celltypist_model="NoSuchModel_xyz.pkl")
    assert result["errors"]
    assert "CellTypist failed" in result["errors"][0]
    assert "models" in result["evidence"], "a bad choice should come with the valid ones"


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

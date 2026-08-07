"""Tests for `run_clustering`.

Run with `python tests/test_run_clustering.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

clus = load_skill("run_clustering")


def _adata(n_cells=200, n_genes=20, n_comps=10, *, key="X_pca", n_blobs=3):
    """An embedding with real cluster structure, not just noise."""
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    centers = rng.normal(scale=8, size=(n_blobs, n_comps))
    assignments = rng.integers(0, n_blobs, size=n_cells)
    embedding = centers[assignments] + rng.normal(scale=0.5, size=(n_cells, n_comps))

    adata = anndata.AnnData(sp.csr_matrix(np.zeros((n_cells, n_genes), dtype="float32")))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.obsm[key] = embedding.astype("float32")
    return adata


def _run_via_integration(root: Path, adata, embedding_key="X_pca", **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return clus.run(
        {
            "artifacts": {
                "run_integration": {
                    "adata_path": str(path),
                    "integration_summary": {"embedding_key": embedding_key},
                }
            },
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the basic shape ---------------------------------------------------------


def test_clusters_a_structured_embedding():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run_via_integration(root, _adata(n_blobs=3))
        assert result["errors"] == []
        assert result["clustering_summary"]["n_clusters"] >= 2
        written = anndata.read_h5ad(result["adata_path"])
        assert "leiden" in written.obs
        assert written.obs["leiden"].nunique() == result["clustering_summary"]["n_clusters"]


def test_reads_embedding_key_from_run_integration():
    """A batch-corrected object should be clustered on X_pca_harmony, not X_pca."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_via_integration(
            Path(tmp), _adata(key="X_pca_harmony", n_blobs=3), embedding_key="X_pca_harmony"
        )
    assert result["errors"] == []
    assert result["clustering_summary"]["embedding_key"] == "X_pca_harmony"


def _override_the_correction(root: Path):
    """Integration recommends X_pca_harmony; config insists on X_pca.

    Called directly rather than through `_run_via_integration`, whose
    `embedding_key` argument is the recommendation — the whole point here is
    that the recommendation and the config key are two different things.
    """
    adata = _adata(key="X_pca", n_blobs=3)
    adata.obsm["X_pca_harmony"] = adata.obsm["X_pca"].copy()
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return clus.run({
        "artifacts": {
            "run_integration": {
                "adata_path": str(path),
                "integration_summary": {"embedding_key": "X_pca_harmony"},
            }
        },
        "run_dir": str(root / "run"),
        "config": {"embedding_key": "X_pca"},
    })


def test_where_the_embedding_came_from_is_recorded():
    """`embedding_key` alone cannot tell a correct basis from an overridden one.

    A run with nothing to correct and a run that corrected a batch then
    clustered around the correction both report `X_pca`, with every other
    number identical. Only the provenance separates them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        integrated = _run_via_integration(
            Path(tmp), _adata(key="X_pca_harmony", n_blobs=3),
            embedding_key="X_pca_harmony")
    summary = integrated["clustering_summary"]
    assert summary["integration_ran"] is True
    assert summary["integration_recommended"] == "X_pca_harmony"
    assert summary["embedding_source"] == "run_integration"

    with tempfile.TemporaryDirectory() as tmp:
        summary = _override_the_correction(Path(tmp))["clustering_summary"]
    assert summary["embedding_key"] == "X_pca"
    assert summary["integration_ran"] is True
    assert summary["integration_recommended"] == "X_pca_harmony"
    assert summary["embedding_source"] == "config override"


def test_clustering_past_a_correction_is_warned_about():
    """The failure is invisible in the result — same cluster count either way."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _override_the_correction(Path(tmp))

    assert result["errors"] == []
    assert any("run_integration recommended" in w for w in result["warnings"]), \
        f"no warning about discarding the correction: {result['warnings']}"


def test_no_integration_on_x_pca_is_not_flagged():
    """Nothing to correct is not the same failure, and must not read as one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = matrix_io.write_h5ad(_adata(n_blobs=3), root / "in.h5ad")
        result = clus.run({
            "artifacts": {"run_pca": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": {},
        })

    summary = result["clustering_summary"]
    assert summary["integration_ran"] is False
    assert summary["embedding_source"] == "run_pca"
    assert not [w for w in result["warnings"] if "run_integration recommended" in w]


def test_resolution_is_configurable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata(n_blobs=6)
        low = _run_via_integration(root, adata, resolution=0.1)
        high = _run_via_integration(root, adata, resolution=2.0)
    assert low["clustering_summary"]["n_clusters"] <= high["clustering_summary"]["n_clusters"]


def test_default_resolution_matches_scanpys_own_default():
    from skills.run_clustering import run_clustering

    with tempfile.TemporaryDirectory() as tmp:
        result = _run_via_integration(Path(tmp), _adata(n_blobs=3))
    assert result["clustering_summary"]["resolution"] == run_clustering.DEFAULT_RESOLUTION


# --- notes on degenerate outcomes ---------------------------------------------


def test_a_single_cluster_gets_a_note_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        # No structure at all: one blob, tight, at a very low resolution.
        result = _run_via_integration(Path(tmp), _adata(n_blobs=1), resolution=0.01)
    assert result["errors"] == []
    if result["clustering_summary"]["n_clusters"] < 2:
        assert any("produced" in n for n in result["notes"])


# --- neighbor count is bounded -------------------------------------------------


def test_n_neighbors_above_cell_count_is_clamped_not_refused():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_via_integration(Path(tmp), _adata(n_cells=10, n_blobs=2), n_neighbors=50)
    assert result["errors"] == []
    assert any("using" in w for w in result["warnings"])
    assert result["clustering_summary"]["n_neighbors"] == 9


# --- failures ------------------------------------------------------------------


def test_missing_path_is_an_error():
    result = clus.run({"artifacts": {}, "run_dir": "."})
    assert any("run_integration must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = clus.run(
        {
            "artifacts": {"run_integration": {
                "adata_path": "/nope.h5ad",
                "integration_summary": {"embedding_key": "X_pca"},
            }},
            "run_dir": ".",
        }
    )
    assert any("does not exist" in e for e in result["errors"])


def test_missing_embedding_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata()
        del adata.obsm["X_pca"]
        path = matrix_io.write_h5ad(adata, root / "in.h5ad")
        result = clus.run(
            {
                "artifacts": {"run_integration": {
                    "adata_path": str(path),
                    "integration_summary": {"embedding_key": "X_pca"},
                }},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
    assert any("run_integration must run first" in e for e in result["errors"])


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

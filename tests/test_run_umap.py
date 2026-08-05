"""Tests for `run_umap`.

Run with `python tests/test_run_umap.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

um = load_skill("run_umap")


def _adata(n_cells=200, n_genes=20, n_comps=10, *, with_neighbors=True, key="X_pca"):
    """An embedding with cluster structure, optionally with a neighbor graph."""
    import anndata
    import numpy as np
    import scanpy as sc
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    centers = rng.normal(scale=8, size=(3, n_comps))
    assignments = rng.integers(0, 3, size=n_cells)
    embedding = centers[assignments] + rng.normal(scale=0.5, size=(n_cells, n_comps))

    adata = anndata.AnnData(sp.csr_matrix(np.zeros((n_cells, n_genes), dtype="float32")))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.obsm[key] = embedding.astype("float32")
    if with_neighbors:
        sc.pp.neighbors(adata, use_rep=key, n_neighbors=15)
    return adata


def _run(root: Path, adata, embedding_key="X_pca", **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return um.run(
        {
            "artifacts": {
                "run_clustering": {
                    "adata_path": str(path),
                    "clustering_summary": {"embedding_key": embedding_key},
                }
            },
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- method selection ----------------------------------------------------------


def test_default_method_is_umap():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata())
        assert result["errors"] == []
        assert result["embedding_summary"]["computed"] == ["umap"]
        written = anndata.read_h5ad(result["adata_path"])
        assert "X_umap" in written.obsm
        assert "X_tsne" not in written.obsm


def test_tsne_method_reads_embedding_key_not_neighbors():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # No neighbor graph at all: t-SNE must not need it.
        result = _run(root, _adata(with_neighbors=False), method="tsne")
        assert result["errors"] == []
        assert result["embedding_summary"]["computed"] == ["tsne"]
        written = anndata.read_h5ad(result["adata_path"])
        assert "X_tsne" in written.obsm
        assert "X_umap" not in written.obsm


def test_both_computes_both_and_neither_overwrites_the_other():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(), method="both")
        assert result["errors"] == []
        assert set(result["embedding_summary"]["computed"]) == {"umap", "tsne"}
        written = anndata.read_h5ad(result["adata_path"])
        assert "X_umap" in written.obsm and "X_tsne" in written.obsm


def test_invalid_method_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), method="pca")
    assert any("is not one of" in e for e in result["errors"])


# --- the integration diagnostic -------------------------------------------------


def _integrated(root: Path, adata, *, integrated=True, batch_key="sample", **config):
    """Run as if run_integration had corrected batches, or had skipped."""
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return um.run(
        {
            "artifacts": {
                "run_integration": {
                    "adata_path": str(path),
                    "integration_summary": {
                        "integrated": integrated,
                        "batch_key": batch_key,
                        "embedding_key": "X_pca_harmony" if integrated else "X_pca",
                    },
                },
                "run_clustering": {
                    "adata_path": str(path),
                    "clustering_summary": {
                        "embedding_key": "X_pca_harmony" if integrated else "X_pca",
                        "n_neighbors": 15,
                    },
                },
            },
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


def _two_batch_adata(n_cells=200):
    import pandas as pd

    adata = _adata(n_cells, with_neighbors=True, key="X_pca")
    import numpy as np

    rng = np.random.default_rng(1)
    adata.obsm["X_pca_harmony"] = rng.normal(size=(n_cells, 10)).astype("float32")
    adata.obs["sample"] = pd.Categorical([f"S{i % 2}" for i in range(n_cells)])
    return adata


def test_the_pre_integration_embedding_is_computed_when_batches_were_corrected():
    """Saved at analysis time, so the report renders rather than recomputes."""
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _integrated(root, _two_batch_adata())
        assert result["errors"] == []
        assert "umap_unintegrated" in result["embedding_summary"]["computed"]
        assert result["embedding_summary"]["unintegrated_umap_key"] == "X_umap_unintegrated"
        written = anndata.read_h5ad(result["adata_path"])
        assert "X_umap_unintegrated" in written.obsm
        assert written.obsm["X_umap_unintegrated"].shape == written.obsm["X_umap"].shape


def test_the_mainline_neighbor_graph_is_not_overwritten():
    """Clustering's graph has to survive: the report reads both."""
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _integrated(root, _two_batch_adata())
        written = anndata.read_h5ad(result["adata_path"])
    assert "neighbors" in written.uns
    assert "neighbors_unintegrated" in written.uns


def test_nothing_is_computed_when_integration_was_skipped():
    """A before/after picture of one library compares an embedding to itself."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _integrated(Path(tmp), _two_batch_adata(), integrated=False)
    assert result["errors"] == []
    assert "umap_unintegrated" not in result["embedding_summary"]["computed"]
    assert result["embedding_summary"]["unintegrated_umap_key"] is None


def test_nothing_is_computed_for_a_single_batch():
    import pandas as pd

    adata = _two_batch_adata()
    adata.obs["sample"] = pd.Categorical(["only"] * adata.n_obs)
    with tempfile.TemporaryDirectory() as tmp:
        result = _integrated(Path(tmp), adata)
    assert result["embedding_summary"]["unintegrated_umap_key"] is None


# --- UMAP needs the neighbor graph ---------------------------------------------


def test_umap_without_a_neighbor_graph_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(with_neighbors=False), method="umap")
    assert any("no neighbor graph" in e for e in result["errors"])


# --- t-SNE perplexity is bounded ------------------------------------------------


def test_perplexity_above_the_bound_is_clamped_not_refused():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _adata(n_cells=20, with_neighbors=False), method="tsne", perplexity=30
        )
    assert result["errors"] == []
    assert any("using" in w for w in result["warnings"])
    assert result["embedding_summary"]["computed"] == ["tsne"]


def test_small_n_gets_a_note_about_tsne_reliability():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(n_cells=20, with_neighbors=False), method="tsne")
    assert any("unreliable" in n for n in result["notes"])


# --- failures ------------------------------------------------------------------


def test_missing_path_is_an_error():
    result = um.run({"artifacts": {}, "run_dir": "."})
    assert any("run_clustering must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = um.run(
        {
            "artifacts": {"run_clustering": {
                "adata_path": "/nope.h5ad",
                "clustering_summary": {"embedding_key": "X_pca"},
            }},
            "run_dir": ".",
        }
    )
    assert any("does not exist" in e for e in result["errors"])


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

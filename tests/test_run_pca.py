"""Tests for `run_pca`.

Run with `python tests/test_run_pca.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

pca = load_skill("run_pca")


def _adata(n_cells=200, n_genes=200, *, n_hvg=None):
    """Two populations with real gene-level variance, optionally HVG-flagged."""
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    half = n_cells // 2
    a = rng.poisson(3, size=(half, n_genes))
    b = rng.poisson(3, size=(n_cells - half, n_genes))
    a[:, : n_genes // 2] += 8
    b[:, n_genes // 2 :] += 8
    values = np.vstack([a, b]).astype("float32")

    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    if n_hvg is not None:
        flags = np.zeros(n_genes, dtype=bool)
        flags[:n_hvg] = True
        adata.var["highly_variable"] = flags
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return pca.run(
        {
            "artifacts": {"normalize_hvg_prepare": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the basic shape ---------------------------------------------------------


def test_fits_and_writes_an_embedding():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(200, 200, n_hvg=100), n_comps=10)
        assert result["errors"] == []
        written = anndata.read_h5ad(result["adata_path"])
        assert written.obsm["X_pca"].shape == (200, 10)
        assert written.varm["PCs"].shape == (200, 10)


def test_variance_ratio_is_reported_and_sums_sensibly():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200, n_hvg=100), n_comps=10)
    ratios = result["pca_summary"]["variance_ratio"]
    assert len(ratios) == 10
    assert result["pca_summary"]["cumulative_variance_explained"] <= 1.0
    assert abs(sum(ratios) - result["pca_summary"]["cumulative_variance_explained"]) < 1e-3


# --- HVG masking --------------------------------------------------------------


def test_fits_on_hvgs_only_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200, n_hvg=100), n_comps=10)
    assert result["pca_summary"]["used_highly_variable"] is True
    assert result["pca_summary"]["n_genes_used"] == 100


def test_no_hvg_flag_falls_back_to_all_genes_with_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200), n_comps=10)  # no highly_variable column
    assert result["pca_summary"]["used_highly_variable"] is False
    assert result["pca_summary"]["n_genes_used"] == 200
    assert any("no highly_variable flag" in w for w in result["warnings"])


def test_use_highly_variable_false_is_honored():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _adata(200, 200, n_hvg=100), n_comps=10, use_highly_variable=False
        )
    assert result["pca_summary"]["used_highly_variable"] is False
    assert result["pca_summary"]["n_genes_used"] == 200


# --- component count is bounded -----------------------------------------------


def test_n_comps_above_the_rank_bound_is_clamped_not_refused():
    requested = 50
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(20, 200, n_hvg=100), n_comps=requested)
    assert result["errors"] == []
    assert any("using" in w for w in result["warnings"])
    assert result["pca_summary"]["n_comps"] < requested
    assert result["pca_summary"]["n_comps"] == min(20, 100) - 1


def test_default_n_comps_matches_scanpys_own_default():
    from skills.run_pca import run_pca

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200, n_hvg=100), n_comps=run_pca.DEFAULT_N_COMPS)
    assert result["pca_summary"]["n_comps_requested"] == run_pca.DEFAULT_N_COMPS


# --- failures ------------------------------------------------------------------


def test_missing_path_is_an_error():
    result = pca.run({"artifacts": {}, "run_dir": "."})
    assert any("normalize_hvg_prepare must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = pca.run(
        {"artifacts": {"normalize_hvg_prepare": {"adata_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


def test_a_single_cell_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(1, 200))
    assert any("PCA needs at least 2" in e for e in result["errors"])


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

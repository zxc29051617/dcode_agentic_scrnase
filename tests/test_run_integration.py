"""Tests for `run_integration`.

Run with `python tests/test_run_integration.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

integ = load_skill("run_integration")


def _adata(n_cells=200, n_genes=50, n_comps=10, *, samples=None):
    """A fitted-looking PCA embedding, with an optional `sample` column."""
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    half = n_cells // 2
    a = rng.poisson(3, size=(half, n_genes))
    b = rng.poisson(3, size=(n_cells - half, n_genes))
    values = np.vstack([a, b]).astype("float32")

    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.obsm["X_pca"] = rng.normal(size=(n_cells, n_comps)).astype("float32")
    if samples:
        adata.obs["sample"] = [samples[i % len(samples)] for i in range(n_cells)]
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return integ.run(
        {
            "artifacts": {"run_pca": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- deciding whether to integrate --------------------------------------------


def test_a_single_sample_is_skipped_not_integrated():
    with tempfile.TemporaryDirectory() as tmp:
        result = integ.run(
            {
                "artifacts": {"run_pca": {"adata_path": str(
                    matrix_io.write_h5ad(_adata(200, samples=["A"]), Path(tmp) / "in.h5ad")
                )}},
                "run_dir": str(Path(tmp) / "run"),
                "config": {},
            }
        )
    assert result["errors"] == []
    assert result["integration_summary"]["integrated"] is False
    assert result["integration_summary"]["embedding_key"] == "X_pca"
    assert any("only one value" in n for n in result["notes"])


def test_no_batch_key_at_all_is_skipped_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200))  # no `sample` column
    assert result["errors"] == []
    assert result["integration_summary"]["integrated"] is False
    assert any("no obs['sample']" in n for n in result["notes"])


def test_multiple_samples_with_enough_cells_are_integrated():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(200, samples=["A", "B"]))
        assert result["errors"] == []
        assert result["integration_summary"]["integrated"] is True
        assert result["integration_summary"]["embedding_key"] == "X_pca_harmony"
        assert result["integration_summary"]["n_batches"] == 2

        import anndata

        written = anndata.read_h5ad(result["adata_path"])
        assert "X_pca_harmony" in written.obsm
        assert written.obsm["X_pca_harmony"].shape == written.obsm["X_pca"].shape


def test_x_is_never_touched_by_integration():
    """Harmony corrects the embedding, not the expression matrix."""
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw = _adata(200, samples=["A", "B"])
        raw_x = np.asarray(raw.X.todense())
        result = _run(root, raw)
        import anndata

        written = anndata.read_h5ad(result["adata_path"])
        assert np.allclose(np.asarray(written.X.todense()), raw_x)


def test_a_batch_too_small_is_skipped_with_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, samples=["A"] * 195 + ["B"] * 5))
    assert result["errors"] == []
    assert result["integration_summary"]["integrated"] is False
    assert any("fewer than" in w for w in result["warnings"])


def test_force_integration_runs_harmony_even_on_one_sample():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, samples=["A"]), force_integration=True)
    assert result["errors"] == []
    assert result["integration_summary"]["integrated"] is True


# --- failures ----------------------------------------------------------------


def test_missing_path_is_an_error():
    result = integ.run({"artifacts": {}, "run_dir": "."})
    assert any("run_pca must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = integ.run(
        {"artifacts": {"run_pca": {"adata_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


def test_missing_pca_embedding_is_an_error():
    import anndata
    import numpy as np
    import scipy.sparse as sp

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = anndata.AnnData(sp.csr_matrix(np.zeros((10, 5), dtype="float32")))
        path = matrix_io.write_h5ad(adata, root / "in.h5ad")
        result = integ.run(
            {
                "artifacts": {"run_pca": {"adata_path": str(path)}},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
    assert any("run_pca must run first" in e for e in result["errors"])


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

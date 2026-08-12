"""Tests for `normalize_hvg_prepare`.

Run with `python tests/test_normalize_hvg_prepare.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

norm = load_skill("normalize_hvg_prepare")


def _adata(n_cells=200, n_genes=200, *, samples=None):
    """Two populations with real gene-level variance, and a raw `counts` layer.

    `post_load_validate` is what normally puts `layers["counts"]` on the
    object; this step refuses to run without it, so the fixture supplies one
    directly rather than replaying every upstream step.
    """
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
    adata.layers["counts"] = adata.X.copy()
    if samples:
        adata.obs["sample"] = [samples[i % len(samples)] for i in range(n_cells)]
    return adata


def _run(root: Path, adata, **config):
    config.setdefault("n_top_genes", 50)  # fixtures are far smaller than 2,000 genes
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return norm.run(
        {
            "artifacts": {"detect_doublets": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the basic shape ---------------------------------------------------------


def test_normalizes_logs_and_flags_hvgs():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(200, 200))
        assert result["errors"] == []
        written = anndata.read_h5ad(result["adata_path"])
        assert "highly_variable" in written.var
        assert int(written.var["highly_variable"].sum()) == 50


def test_raw_counts_survive_untouched_in_the_layer():
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw = _adata(200, 200)
        raw_total = float(np.asarray(raw.layers["counts"].sum()))
        result = _run(root, raw)
        import anndata

        written = anndata.read_h5ad(result["adata_path"])
        assert float(np.asarray(written.layers["counts"].sum())) == raw_total, (
            "layers['counts'] must stay raw; only X is normalized"
        )
        assert float(written.X.max()) < float(written.layers["counts"].max()), (
            "X should be log-transformed, not still raw integers"
        )


def test_x_is_normalized_and_log_transformed():
    import anndata
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(200, 200))
        written = anndata.read_h5ad(result["adata_path"])
    # log1p of non-negative values is non-negative; a raw integer matrix would
    # not be, since normalize_total rescales per cell to a shared target.
    assert float(written.X.min()) >= 0.0
    row_sums = np.expm1(written.X).sum(axis=1)
    row_sums = np.asarray(row_sums).ravel()
    assert np.allclose(row_sums, row_sums[0], rtol=1e-3), "every cell should share the same target depth"


# --- gene filtering ------------------------------------------------------


def test_low_count_genes_are_dropped_before_hvg_selection():
    import anndata
    import numpy as np
    import scipy.sparse as sp

    adata = _adata(200, 200)
    # A gene detected in exactly one cell: below the min_cells_per_gene default.
    x = adata.X.toarray()
    x[:, 0] = 0
    x[0, 0] = 5
    adata.X = sp.csr_matrix(x)
    adata.layers["counts"] = adata.X.copy()

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), adata)
        assert result["hvg_summary"]["n_genes_dropped"] >= 1
        written = anndata.read_h5ad(result["adata_path"])
        assert "GENE0" not in written.var_names


def test_a_threshold_that_drops_every_gene_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200), min_cells_per_gene=10_000)
    assert result["errors"]
    assert "no genes" in result["errors"][0]


# --- HVG count and batching ------------------------------------------------


def test_n_top_genes_larger_than_available_is_clamped_not_refused():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 60), n_top_genes=5_000)
    assert result["errors"] == []
    assert any("using" in w for w in result["warnings"])
    assert result["hvg_summary"]["n_hvg"] < 5_000


def test_multiple_samples_use_batch_key():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200, samples=["A", "B"]))
    assert result["hvg_summary"]["batch_key"] == "sample"


def test_a_single_sample_runs_without_a_batch_key():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, 200, samples=["A"]))
    assert result["hvg_summary"]["batch_key"] is None
    assert any("one library" in n for n in result["notes"])


# --- failures ----------------------------------------------------------------


def test_missing_path_is_an_error():
    result = norm.run({"artifacts": {}, "run_dir": "."})
    assert any("detect_doublets must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = norm.run(
        {"artifacts": {"detect_doublets": {"adata_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


def test_missing_counts_layer_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata(50, 50)
        del adata.layers["counts"]
        path = matrix_io.write_h5ad(adata, root / "in.h5ad")
        result = norm.run(
            {
                "artifacts": {"detect_doublets": {"adata_path": str(path)}},
                "run_dir": str(root / "run"),
                "config": {"n_top_genes": 20},
            }
        )
    assert any("post_load_validate must run first" in e for e in result["errors"])


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

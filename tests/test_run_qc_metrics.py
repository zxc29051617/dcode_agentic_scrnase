"""Tests for `run_qc_metrics`: the first mainline step with real numbers.

Run with `python tests/test_run_qc_metrics.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402
from tests import paths  # noqa: E402

qc = load_skill("run_qc_metrics")

HUMAN_MITO = ("MT-ND1", "MT-ND2", "MT-CO1", "MT-CO2", "MT-ATP8")
HUMAN_ERY = ("HBA1", "HBA2", "HBB", "HBM", "HBD", "ALAS2")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _adata(n_cells=50, extra_genes=(), sample=None):
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    genes = [f"GENE{i}" for i in range(30)] + list(extra_genes)
    values = rng.poisson(5, size=(n_cells, len(genes))).astype("float32")
    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = genes
    if sample is not None:
        adata.obs["sample"] = sample
    return adata


def _run(root: Path, adata, **artifacts_extra):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    artifacts = {"post_load_validate": {"adata_path": str(path)}}
    artifacts.update(artifacts_extra)
    return qc.run({"artifacts": artifacts, "run_dir": str(root / "run"), "config": {}})


# --- the measurement itself --------------------------------------------------


def test_basic_metrics_are_computed():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(50))
    assert result["errors"] == []
    m = result["qc_metrics"]
    assert m["n_cells"] == 50
    assert m["median_genes_per_cell"] > 0
    assert m["median_umi_per_cell"] > 0


def test_mitochondrial_fraction_is_computed_when_genes_are_present():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp),
            _adata(50, extra_genes=HUMAN_MITO),
            resolve_reference={"mito_prefix": "MT-"},
        )
    assert result["mito_computed"] is True
    assert "median_pct_mito" in result["qc_metrics"]
    assert result["qc_metrics"]["median_pct_mito"] > 0
    assert result["warnings"] == []


def test_missing_mito_prefix_is_a_warning_not_a_silent_zero():
    """Reporting 0% mito with no prefix known would look like clean data."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(50))
    assert result["mito_computed"] is False
    assert "median_pct_mito" not in result["qc_metrics"], "absent, not falsely zero"
    assert any("no mitochondrial gene prefix known" in w for w in result["warnings"])


def test_a_prefix_that_matches_nothing_is_flagged():
    """The genes just aren't there — a naming-convention mismatch, not silence."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _adata(50), resolve_reference={"mito_prefix": "MT-"}
        )
    assert result["mito_computed"] is False
    assert any("no genes matched the mitochondrial prefix" in w for w in result["warnings"])


def test_erythroid_fraction_is_computed_when_genes_are_present():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp),
            _adata(50, extra_genes=HUMAN_ERY),
            resolve_reference={"erythroid_genes": list(HUMAN_ERY)},
        )
    assert result["erythroid_computed"] is True
    assert "median_pct_erythroid" in result["qc_metrics"]


def test_erythroid_genes_configured_but_absent_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _adata(50), resolve_reference={"erythroid_genes": list(HUMAN_ERY)}
        )
    assert result["erythroid_computed"] is False
    assert any("none of the" in w and "erythroid genes" in w for w in result["warnings"])


def test_matrix_preflight_constants_work_the_same_as_resolve_reference():
    """Both entry steps emit the same shape; this step must not prefer one."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp),
            _adata(50, extra_genes=HUMAN_MITO),
            matrix_preflight={"mito_prefix": "MT-"},
        )
    assert result["mito_computed"] is True


# --- per-sample breakdown ----------------------------------------------------


def test_per_sample_breakdown_reflects_real_differences():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        import anndata

        a = _adata(30, sample="A")
        b = _adata(60, sample="B")
        merged = anndata.concat([a, b], index_unique="-")
        result = _run(root, merged)
    assert result["errors"] == []
    assert set(result["per_sample"]) == {"A", "B"}
    assert result["per_sample"]["A"]["n_cells"] == 30
    assert result["per_sample"]["B"]["n_cells"] == 60


def test_no_sample_column_skips_the_breakdown_not_the_run():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(20))
    assert result["errors"] == []
    assert result["per_sample"] == {}


# --- failures -----------------------------------------------------------------


def test_missing_path_is_an_error():
    result = qc.run({"artifacts": {}, "run_dir": "."})
    assert any("post_load_validate must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = qc.run(
        {"artifacts": {"post_load_validate": {"adata_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


# --- real data ----------------------------------------------------------------


def test_real_pbmc_1k_v3_matches_cell_rangers_own_metric():
    """An independent check: our median should equal Cell Ranger's own report."""
    source = paths.COUNT_OUTS / "pbmc_1k_v3" / "outs" / "filtered_feature_bc_matrix.h5"
    if not source.is_file():
        raise Skip("pbmc_1k_v3 has not been counted (see data/README.md)")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = qc.run(
            {
                "artifacts": {
                    "post_load_validate": {"adata_path": str(source)},
                    "resolve_reference": {
                        "mito_prefix": "MT-",
                        "erythroid_genes": list(HUMAN_ERY),
                    },
                },
                "run_dir": str(root),
                "config": {},
            }
        )
    assert result["errors"] == []
    # Cell Ranger's own metrics_summary.csv reports 3,201 median genes per cell.
    assert result["qc_metrics"]["median_genes_per_cell"] == 3_201
    assert result["mito_computed"] is True
    assert 0 < result["qc_metrics"]["median_pct_mito"] < 100


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

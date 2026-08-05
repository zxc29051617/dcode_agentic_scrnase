"""Tests for `find_markers`.

Run with `python tests/test_find_markers.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

fm = load_skill("find_markers")


def _adata(n_cells=120, n_genes=60, *, cluster_sizes=None, planted=True):
    """Log-normalized expression with genes planted to mark specific clusters.

    `cluster_sizes` is a list of cell counts, one per cluster. Each cluster
    gets its own block of genes over-expressed only in it, so a correct
    ranking has an obvious right answer to check against.
    """
    import anndata
    import numpy as np
    import pandas as pd
    import scanpy as sc
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    if cluster_sizes is None:
        cluster_sizes = [n_cells // 3] * 3
    n_cells = sum(cluster_sizes)

    counts = rng.poisson(3, size=(n_cells, n_genes)).astype("float32")
    labels: list[str] = []
    start = 0
    per_block = max(1, n_genes // (len(cluster_sizes) * 2))
    for index, size in enumerate(cluster_sizes):
        labels += [str(index)] * size
        if planted:
            block = slice(index * per_block, (index + 1) * per_block)
            counts[start : start + size, block] += 50
        start += size

    adata = anndata.AnnData(sp.csr_matrix(counts))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.layers["counts"] = adata.X.copy()
    adata.obs["leiden"] = pd.Categorical(labels)
    sc.pp.normalize_total(adata)
    sc.pp.log1p(adata)
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return fm.run(
        {
            "artifacts": {"run_umap": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the basic shape ---------------------------------------------------------


def test_finds_the_planted_marker_for_each_cluster():
    """Genes planted in exactly one cluster should rank top for that cluster."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata())
    assert result["errors"] == []
    per_block = 60 // (3 * 2)
    for index in range(3):
        expected = {f"GENE{g}" for g in range(index * per_block, (index + 1) * per_block)}
        top = {m["gene"] for m in result["top_markers"][str(index)][:per_block]}
        assert top & expected, f"cluster {index} did not surface its planted genes: {top}"


def test_the_full_table_is_written_to_disk_not_returned():
    import csv

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), n_genes_reported=5)
        assert Path(result["marker_table_path"]).exists()
        with open(result["marker_table_path"], newline="") as handle:
            rows = list(csv.DictReader(handle))
        # Every gene for every cluster is on disk; only 5 per cluster come back.
        assert len(rows) == 60 * 3
        assert all(len(v) == 5 for v in result["top_markers"].values())


def test_expression_fractions_are_reported():
    """annotate_cells needs to know a marker is on in the cluster and off elsewhere."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata())
    top = result["top_markers"]["0"][0]
    assert 0.0 <= top["pct_in_cluster"] <= 1.0
    assert 0.0 <= top["pct_in_rest"] <= 1.0
    assert top["pct_in_cluster"] > top["pct_in_rest"]


def test_all_genes_are_tested_not_just_hvgs():
    """normalize_hvg_prepare flags HVGs without subsetting so this can see everything."""
    import numpy as np

    adata = _adata()
    flags = np.zeros(adata.n_vars, dtype=bool)
    flags[:5] = True  # only 5 genes flagged; markers must not be limited to them
    adata.var["highly_variable"] = flags
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), adata)
    assert result["marker_summary"]["n_genes_tested"] == 60


# --- degenerate clusters -------------------------------------------------------


def test_a_one_cell_cluster_is_excluded_instead_of_aborting_every_cluster():
    """scanpy raises for a singleton group, and the failure takes the rest with it."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(cluster_sizes=[40, 40, 1]))
    assert result["errors"] == []
    assert result["marker_summary"]["clusters_excluded"] == {"2": 1}
    assert result["marker_summary"]["n_clusters_tested"] == 2
    assert any("fewer than" in w for w in result["warnings"])
    assert set(result["top_markers"]) == {"0", "1"}


def test_fewer_than_two_testable_clusters_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(cluster_sizes=[60, 1]))
    assert any("needs at least two" in e for e in result["errors"])


def test_clusters_with_no_significant_gene_get_a_note():
    """Random data has no real markers; splitting it should say so, not stay silent."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(planted=False))
    assert result["errors"] == []
    if any(v == 0 for v in result["marker_summary"]["n_significant_per_cluster"].values()):
        assert any("no gene below adjusted p" in n for n in result["notes"])


# --- failures ------------------------------------------------------------------


def test_missing_path_is_an_error():
    result = fm.run({"artifacts": {}, "run_dir": "."})
    assert any("run_umap must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = fm.run({"artifacts": {"run_umap": {"adata_path": "/nope.h5ad"}}, "run_dir": "."})
    assert any("does not exist" in e for e in result["errors"])


def test_missing_cluster_labels_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata()
        del adata.obs["leiden"]
        path = matrix_io.write_h5ad(adata, root / "in.h5ad")
        result = fm.run(
            {
                "artifacts": {"run_umap": {"adata_path": str(path)}},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
    assert any("run_clustering must run first" in e for e in result["errors"])


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

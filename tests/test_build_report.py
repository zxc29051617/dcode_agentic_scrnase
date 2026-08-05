"""Tests for `build_report` and `src/plots.py`.

The behaviour worth pinning is not what the figures look like — it is that the
report never lies about what it has: unmet conditions are stated, a run that
stopped early still produces something, and the two renderings come from one
model so they cannot disagree.

Run with `python tests/test_build_report.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io, plots  # noqa: E402
from src.registry import load_skill  # noqa: E402

report = load_skill("build_report")


def _adata(n_cells=120, n_genes=40, *, samples=("A", "B")):
    """An object carrying what the later sections read."""
    import anndata
    import numpy as np
    import pandas as pd
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    counts = rng.poisson(4, size=(n_cells, n_genes)).astype("float32")
    adata = anndata.AnnData(sp.csr_matrix(counts))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.layers["counts"] = adata.X.copy()
    adata.obs["sample"] = pd.Categorical([samples[i % len(samples)] for i in range(n_cells)])
    adata.obs["leiden"] = pd.Categorical([str(i % 3) for i in range(n_cells)])
    adata.obs["n_genes_by_counts"] = np.linspace(100, 3_000, n_cells)
    adata.obs["total_counts"] = np.linspace(500, 20_000, n_cells)
    adata.obs["pct_counts_mt"] = np.linspace(0, 30, n_cells)
    adata.obs["doublet_score"] = rng.random(n_cells)
    adata.obsm["X_umap"] = rng.normal(size=(n_cells, 2)).astype("float32")
    return adata


def _run(root: Path, artifacts: dict, **config):
    return report.run({"artifacts": artifacts, "run_dir": str(root), "config": config})


# --- honest about what is missing ---------------------------------------------


def test_an_empty_run_still_produces_a_report():
    """A run that got nowhere still deserves a document saying so."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), {})
        assert result["errors"] == []
        assert Path(result["markdown_path"]).exists()
        assert Path(result["html_path"]).exists()
    assert result["metrics"]["n_available"] < result["metrics"]["n_sections"]


def test_every_unavailable_section_states_a_reason():
    """An absent figure without an explanation reads as an oversight."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {})
        model = json.loads(Path(result["model_path"]).read_text(encoding="utf-8"))
    for section in model["sections"]:
        if not section["available"]:
            assert section["reason"], f"{section['key']} vanished without saying why"


def test_unavailable_sections_appear_in_both_renderings():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {})
        markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")
        page = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "Not available" in markdown
    assert "Not available" in page


# --- one model, two renderings --------------------------------------------------


def test_both_renderings_come_from_the_same_model():
    """Assembling twice is how two documents drift while both keep working."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata()
        path = matrix_io.write_h5ad(adata, root / "final.h5ad")
        result = _run(root, {
            "run_qc_metrics": {"adata_path": str(path)},
            "apply_cell_qc_filter": {"adata_path": str(path),
                                     "filter_summary": {"n_before": 120, "n_after": 120}},
            "annotate_cells": {"adata_path": str(path)},
        })
        model = json.loads(Path(result["model_path"]).read_text(encoding="utf-8"))
        markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")
        page = Path(result["html_path"]).read_text(encoding="utf-8")
    for section in model["sections"]:
        assert section["title"] in markdown, f"{section['key']} missing from markdown"
        assert section["title"] in page, f"{section['key']} missing from html"


def test_the_html_is_self_contained_by_default():
    """One file that can be sent to someone, rather than a folder to zip."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata()
        path = matrix_io.write_h5ad(adata, root / "final.h5ad")
        result = _run(root, {"run_qc_metrics": {"adata_path": str(path)},
                             "apply_cell_qc_filter": {"adata_path": str(path)}})
        page = Path(result["html_path"]).read_text(encoding="utf-8")
    if result["figure_paths"]:
        assert "data:image/png;base64," in page


# --- the report never analyses ----------------------------------------------------


def test_the_report_does_not_mutate_the_object_it_reads():
    """`rank_genes_groups_dotplot` runs a dendrogram unless told not to."""
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _adata()
        path = matrix_io.write_h5ad(adata, root / "final.h5ad")
        before = set(anndata.read_h5ad(path).uns)
        _run(root, {"annotate_cells": {"adata_path": str(path)}})
        after = set(anndata.read_h5ad(path).uns)
    assert before == after, "the report wrote back to a step's object"


# --- plots degrade rather than raise -----------------------------------------------


def test_a_plot_with_nothing_to_draw_returns_none_instead_of_raising():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert plots.retention_funnel([], root / "x.png") is None
        assert plots.marker_dotplot(_adata(), root / "y.png") is None  # no rank_genes_groups
        assert plots.barcode_rank("/nope.npz", {}, root / "z.png") is None


def test_a_drawn_figure_lands_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = plots.retention_funnel([("loaded", 100), ("kept", 80)], root / "funnel.png")
        assert path and Path(path).exists() and Path(path).stat().st_size > 0


def test_the_barcode_rank_curve_is_drawn_from_the_saved_vector():
    """Not by reloading the raw matrix — that is why the vector is saved."""
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = np.sort(np.random.default_rng(0).poisson(50, size=5_000))[::-1]
        npz = root / "curve.npz"
        np.savez_compressed(npz, sorted_umi_counts=counts.astype("int32"))
        path = plots.barcode_rank(npz, {"knee_rank": 100, "inflection_rank": 900},
                                  root / "rank.png", selected_cells=500)
        assert path and Path(path).exists()


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

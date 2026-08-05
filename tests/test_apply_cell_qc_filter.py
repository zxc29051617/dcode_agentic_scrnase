"""Tests for `apply_cell_qc_filter`: where a number becomes a cut.

Run with `python tests/test_apply_cell_qc_filter.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402
from tests import paths  # noqa: E402

filt = load_skill("apply_cell_qc_filter")
qc = load_skill("run_qc_metrics")

HUMAN_MITO = ("MT-ND1", "MT-CO1", "MT-ATP8")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _annotated(n_cells=100, *, samples=None, with_mito=True):
    """An AnnData already carrying QC columns, as run_qc_metrics leaves it."""
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    genes = [f"GENE{i}" for i in range(20)] + (list(HUMAN_MITO) if with_mito else [])
    values = rng.poisson(5, size=(n_cells, len(genes))).astype("float32")
    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = genes
    if samples:
        adata.obs["sample"] = [samples[i % len(samples)] for i in range(n_cells)]

    # A spread wide enough that thresholds actually bite.
    adata.obs["n_genes_by_counts"] = np.linspace(50, 3_000, n_cells)
    adata.obs["total_counts"] = np.linspace(200, 20_000, n_cells)
    if with_mito:
        adata.obs["pct_counts_mt"] = np.linspace(0, 40, n_cells)
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return filt.run(
        {
            "artifacts": {"run_qc_metrics": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the per-cell record of why each cell went ------------------------------


def test_every_pre_filter_cell_gets_a_row_not_only_the_survivors():
    """Flags on the output object would all be False; the removed cells are the point."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), min_genes=500, max_pct_mito=20)
        frame = pd.read_csv(result["filter_summary"]["cell_flags_path"])
    assert len(frame) == 100, "the table covers the cells as they were before the cut"
    assert int(frame["qc_pass"].sum()) == result["filter_summary"]["n_after"]
    assert {"barcode", "qc_pass", "fail_min_genes", "fail_max_pct_mito"} <= set(frame.columns)


def test_criterion_overlap_is_recoverable_from_the_flags():
    """Attribution counts alone cannot say how many cells failed two cuts."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), min_genes=500, max_pct_mito=20)
        frame = pd.read_csv(result["filter_summary"]["cell_flags_path"])

    summary = result["filter_summary"]
    a = int(frame["fail_min_genes"].sum())
    b = int(frame["fail_max_pct_mito"].sum())
    both = int((frame["fail_min_genes"] & frame["fail_max_pct_mito"]).sum())
    assert a == summary["removed_by_criterion"]["min_genes"]
    assert b == summary["removed_by_criterion"]["max_pct_mito"]
    # Inclusion-exclusion has to land exactly on the reported removal count.
    assert a + b - both == summary["n_removed"]
    assert summary["n_removed_by_more_than_one"] == both


def test_per_sample_thresholds_still_produce_whole_object_flags():
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp),
            _annotated(100, samples=["A", "B"]),
            min_genes={"A": 500, "B": 1_500},
        )
        frame = pd.read_csv(result["filter_summary"]["cell_flags_path"])
    assert len(frame) == 100
    assert set(frame["sample"]) == {"A", "B"}
    assert int(frame["qc_pass"].sum()) == result["filter_summary"]["n_after"]


# --- measure first, cut only when told --------------------------------------


def test_no_thresholds_means_no_filtering():
    """A QC cutoff is not guessed on someone's behalf."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated())
    assert result["errors"] == []
    assert result["filter_state"] == "needs_review"
    assert result["adata_path"] is None, "nothing may be handed downstream yet"
    assert result["recommended_next_tool"] == "human_review"
    assert any("no QC thresholds chosen" in w for w in result["warnings"])


def test_the_evidence_prices_each_candidate_threshold():
    """A table of what each value costs is what makes the choice possible."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated())
    preview = result["evidence"]["preview"]
    assert "min_genes" in preview and "max_pct_mito" in preview
    for rows in preview.values():
        assert all({"threshold", "cells_removed", "cells_kept", "pct_removed"} <= set(r) for r in rows)
    removed = [r["cells_removed"] for r in preview["min_genes"]]
    assert removed == sorted(removed), "a higher min_genes cannot remove fewer cells"


def test_distributions_are_reported_for_every_available_criterion():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated())
    dists = result["evidence"]["distributions"]
    assert {"min_genes", "min_counts", "max_pct_mito"} <= set(dists)
    assert "50" in dists["min_genes"]["percentiles"]


# --- applying ----------------------------------------------------------------


def test_a_threshold_removes_cells_and_reports_the_burden():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), min_genes=1_000)
    assert result["filter_state"] == "applied"
    summary = result["filter_summary"]
    assert summary["n_before"] == 100
    assert summary["n_after"] < 100
    assert summary["n_removed"] == summary["n_before"] - summary["n_after"]
    assert result["thresholds"]["chosen_by"] == "operator"


def test_removals_are_attributed_to_the_criterion_that_caused_them():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), min_genes=500, max_pct_mito=20)
    by_criterion = result["filter_summary"]["removed_by_criterion"]
    assert by_criterion["min_genes"] > 0
    assert by_criterion["max_pct_mito"] > 0


def test_a_threshold_that_removes_everything_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), min_genes=999_999)
    assert result["errors"]
    assert "remove every one of the" in result["errors"][0]
    assert result["adata_path"] is None


def test_a_drastic_cut_is_flagged_but_allowed():
    """It may be right for a contaminated sample — but never silent."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(100), max_pct_mito=5)
    assert result["filter_state"] == "applied"
    assert any("most of the data" in w for w in result["warnings"])


# --- the column that is not there --------------------------------------------


def test_filtering_on_an_uncomputed_metric_is_refused():
    """run_qc_metrics omits pct_counts_mt when it could not be measured.

    Passing every cell would look identical to a filter that found nothing to
    remove, so the threshold has to fail loudly instead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _annotated(with_mito=False), max_pct_mito=20)
    assert result["errors"]
    assert "max_pct_mito" in result["errors"][0]
    assert "does not exist" in result["errors"][0]


def test_no_qc_columns_at_all_is_an_error():
    import anndata
    import numpy as np
    import scipy.sparse as sp

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = anndata.AnnData(sp.csr_matrix(np.ones((5, 3), dtype="float32")))
        result = _run(root, bare, min_genes=100)
    assert any("no QC columns to filter on" in e for e in result["errors"])


# --- per sample ---------------------------------------------------------------


def test_per_sample_thresholds_are_applied_independently():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp),
            _annotated(100, samples=["A", "B"]),
            min_genes={"A": 2_000, "B": 100},
        )
    assert result["filter_state"] == "applied"
    a, b = result["per_sample"]["A"], result["per_sample"]["B"]
    assert a["n_after"] < b["n_after"], "the stricter threshold must keep fewer"
    assert a["thresholds"]["min_genes"] == 2_000


def test_a_sample_with_no_threshold_is_kept_and_said_so():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _annotated(100, samples=["A", "B"]), min_genes={"A": 2_000}
        )
    assert result["per_sample"]["B"]["n_after"] == result["per_sample"]["B"]["n_before"]
    assert any("no threshold given for this sample" in w for w in result["warnings"])


def test_one_sample_losing_most_of_its_cells_is_flagged():
    """A threshold set for the whole run can be far harsher on one library."""
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        adata = _annotated(100, samples=["A", "B"])
        # Make A's cells almost all mitochondrial-heavy, B's clean.
        is_a = np.asarray(adata.obs["sample"] == "A")
        adata.obs["pct_counts_mt"] = np.where(is_a, 40.0, 1.0)
        result = _run(Path(tmp), adata, max_pct_mito=10)
    assert any("survived" in w for w in result["warnings"])


# --- failures -----------------------------------------------------------------


def test_missing_path_is_an_error():
    result = filt.run({"artifacts": {}, "run_dir": "."})
    assert any("run_qc_metrics must run first" in e for e in result["errors"])


# --- real data ----------------------------------------------------------------


def test_real_merged_pbmc_shows_the_chemistry_difference():
    """max_pct_mito=5 is near the pooled median, so it cuts very unevenly."""
    sources = {
        name: paths.COUNT_OUTS / name / "outs" / "filtered_feature_bc_matrix.h5"
        for name in ("pbmc_1k_v2", "pbmc_1k_v3")
    }
    if not all(p.is_file() for p in sources.values()):
        raise Skip("both pbmc libraries need counting first (see data/README.md)")

    load = load_skill("load_filtered_counts")
    merge = load_skill("merge_samples")
    validate = load_skill("post_load_validate")
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        loaded = load.run(
            {
                "artifacts": {
                    "count_matrix_classify": {
                        "matrix_paths": {k: str(v) for k, v in sources.items()}
                    }
                },
                "run_dir": root,
                "config": {},
            }
        )
        merged = merge.run({"artifacts": {"load_filtered_counts": loaded}, "run_dir": root, "config": {}})
        standard = validate.run(
            {"artifacts": {"merge_samples": merged}, "run_dir": root, "config": {"species": "human"}}
        )
        measured = qc.run(
            {
                "artifacts": {
                    "post_load_validate": standard,
                    "resolve_reference": {"mito_prefix": "MT-"},
                },
                "run_dir": root,
                "config": {},
            }
        )
        result = filt.run(
            {"artifacts": {"run_qc_metrics": measured}, "run_dir": root,
             "config": {"min_genes": 200, "max_pct_mito": 15}}
        )

        assert result["errors"] == []
        assert result["filter_state"] == "applied"
        assert result["filter_summary"]["n_before"] == 2_233
        # A sane threshold keeps almost everything on this well-behaved data.
        assert result["filter_summary"]["pct_removed"] < 10
        assert set(result["per_sample"]) == {"pbmc_1k_v2", "pbmc_1k_v3"}


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

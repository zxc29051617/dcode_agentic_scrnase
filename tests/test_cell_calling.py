"""Tests for the raw-matrix route: loading, and who decides how many cells.

Run with `python tests/test_cell_calling.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

load_raw = load_skill("load_raw_counts")
load_filtered = load_skill("load_filtered_counts")
review = load_skill("cell_calling_review")

REAL_OUTS = (
    Path.home() / ".claude/jobs/d529e0fc/tmp/cr_verify/cellranger_count/pbmc_1k_v3/outs"
)


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _synthetic(n_cells: int = 300, n_empty: int = 5_000, n_genes: int = 60):
    """A raw-shaped AnnData: a few real cells on top of a mass of empty droplets."""
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    cells = rng.poisson(20, size=(n_cells, n_genes))
    ambient = rng.poisson(0.02, size=(n_empty, n_genes))
    matrix = sp.csr_matrix(np.vstack([cells, ambient]).astype("float32"))
    adata = anndata.AnnData(matrix)
    adata.obs_names = [f"BC{i:06d}-1" for i in range(n_cells + n_empty)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    return adata


# --- barcode-rank evidence --------------------------------------------------


def test_the_cliff_search_ignores_the_ambient_tail():
    """Counts stepping 2->1->0 are vertical on a log axis and would win otherwise."""
    import numpy as np

    totals = np.concatenate([np.full(1_000, 5_000), np.full(200_000, 1)])
    evidence = matrix_io.barcode_rank_evidence(totals)
    assert evidence["cliff_rank"] < 2_000, "the cliff is where cells end, not in the tail"
    assert evidence["cliff_searched_to_rank"] == matrix_io.MAX_PLAUSIBLE_CELLS


def test_the_drop_ratio_separates_a_real_cliff_from_a_slope():
    """No clear cliff is itself the finding: that is when a human should choose.

    A plain log-log slope cannot say this — it depends on where in the searched
    range the cliff falls, so identical curves score differently. The ratio of
    counts an octave either side of the cliff is comparable between runs.
    """
    import numpy as np

    sharp = np.concatenate([np.full(1_000, 5_000), np.full(4_000, 5)])
    gradual = 1_000 * np.exp(-np.arange(5_000) / 1_500)

    assert matrix_io.barcode_rank_evidence(sharp)["cliff_drop_ratio"] > 100
    assert matrix_io.barcode_rank_evidence(gradual)["cliff_drop_ratio"] < 10


def test_force_cells_is_top_n_by_umi():
    import numpy as np

    totals = np.array([10, 500, 3, 900, 40])
    mask, how = matrix_io.select_barcodes(totals, force_cells=2)
    assert list(np.flatnonzero(mask)) == [1, 3]
    assert how["umi_threshold"] == 500


def test_min_umi_keeps_everything_at_or_above_the_threshold():
    import numpy as np

    mask, how = matrix_io.select_barcodes(np.array([10, 500, 3, 900, 40]), min_umi=40)
    assert int(mask.sum()) == 3
    assert how["method"] == "min_umi"


def test_force_cells_cannot_ask_for_more_than_exist():
    import numpy as np

    mask, how = matrix_io.select_barcodes(np.array([10, 0, 0, 5]), force_cells=10)
    assert how["selected"] == 2, "only two barcodes have any counts"


def test_selecting_needs_a_criterion():
    import numpy as np

    try:
        matrix_io.select_barcodes(np.array([1, 2]))
    except ValueError as exc:
        assert "force_cells or min_umi" in str(exc)
    else:
        raise AssertionError("must refuse to select without being told how")


# --- loading ----------------------------------------------------------------


def test_load_raw_never_claims_cell_calling_is_resolved():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        matrix_io.write_h5ad(_synthetic(), root / "raw.h5ad")
        result = load_raw.run(
            {
                "artifacts": {"count_matrix_classify": {"matrix_path": str(root / "raw.h5ad")}},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
        assert result["errors"] == []
        assert result["cell_calling_resolved"] is False
        assert result["recommended_next_tool"] == "cell_calling_review"
        assert Path(result["adata_path"]).is_file()
        assert result["barcode_rank"]["n_barcodes"] == 5_300


def test_load_filtered_flags_an_empty_droplet_rather_than_repairing_it():
    """A filtered matrix with empty barcodes was not filtered by anything."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        adata = _synthetic(n_cells=100, n_empty=3)
        adata.X[100:] = 0
        matrix_io.write_h5ad(adata, root / "f.h5ad")
        result = load_filtered.run(
            {
                "artifacts": {"count_matrix_classify": {"matrix_path": str(root / "f.h5ad")}},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
        assert result["errors"] == []
        assert result["cell_calling_resolved"] is True
        assert any("should contain none" in w for w in result["warnings"])


def test_loading_a_missing_matrix_is_an_error():
    result = load_raw.run(
        {"artifacts": {"count_matrix_classify": {"matrix_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


# --- who decides ------------------------------------------------------------


def _reviewed(root: Path, **config):
    matrix_io.write_h5ad(_synthetic(), root / "raw.h5ad")
    return review.run(
        {
            "artifacts": {"load_raw_counts": {"adata_path": str(root / "raw.h5ad")}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


def test_no_choice_means_no_decision():
    """The step measures; it does not pick a cell count on someone's behalf."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _reviewed(Path(tmp))
    assert result["errors"] == []
    assert result["cell_calling_state"] == "needs_review"
    assert result["adata_path"] is None, "nothing may be handed downstream yet"
    assert result["recommended_next_tool"] == "human_review"
    assert any("no cell count chosen" in w for w in result["warnings"])


def test_the_evidence_prices_each_candidate_count():
    """A table of what N costs in UMIs is what makes the choice possible."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _reviewed(Path(tmp))
    preview = result["evidence"]["preview"]
    assert preview, "must offer candidate counts"
    assert all({"cells", "umi_threshold", "median_umi"} <= set(row) for row in preview)
    thresholds = [row["umi_threshold"] for row in preview]
    assert thresholds == sorted(thresholds, reverse=True), "more cells means a lower bar"
    assert any("cliff" in str(row.get("note", "")) for row in preview)


def test_force_cells_resolves_and_writes_a_subset():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _reviewed(root, force_cells=250)
    assert result["cell_calling_state"] == "resolved"
    assert result["n_cells"] == 250
    assert result["selection"]["method"] == "force_cells"
    assert result["selection"]["chosen_by"] == "operator"


def test_min_umi_is_the_other_way_to_say_it():
    with tempfile.TemporaryDirectory() as tmp:
        result = _reviewed(Path(tmp), min_umi=100)
    assert result["cell_calling_state"] == "resolved"
    assert result["selection"]["method"] == "min_umi"
    assert result["n_cells"] > 0


def test_both_criteria_at_once_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        result = _reviewed(Path(tmp), force_cells=100, min_umi=50)
    assert any("two ways to say the same thing" in e for e in result["errors"])


def test_a_threshold_that_keeps_nothing_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _reviewed(Path(tmp), min_umi=10_000_000)
    assert any("keeps no barcodes" in e for e in result["errors"])


def test_missing_raw_adata_is_an_error():
    result = review.run({"artifacts": {}, "config": {"force_cells": 100}, "run_dir": "."})
    assert any("load_raw_counts must run first" in e for e in result["errors"])


# --- the real matrix --------------------------------------------------------


def _real_raw(root: Path):
    source = REAL_OUTS / "raw_feature_bc_matrix.h5"
    if not source.is_file():
        raise Skip("no real cellranger output on this machine")
    return load_raw.run(
        {
            "artifacts": {"count_matrix_classify": {"matrix_path": str(source)}},
            "run_dir": str(root),
            "config": {},
        }
    )


def test_real_raw_cliff_lands_near_cell_rangers_own_call():
    """An independent check: the curve's cliff should agree with the algorithm."""
    with tempfile.TemporaryDirectory() as tmp:
        loaded = _real_raw(Path(tmp))
        assert loaded["errors"] == []
        cliff = loaded["metrics"]["cliff_rank"]
        # Cell Ranger called 1,218 cells on this dataset.
        assert 1_000 <= cliff <= 1_500, f"cliff at {cliff} is nowhere near 1,218"


def test_real_selection_reports_what_it_gives_up_against_cell_ranger():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        loaded = _real_raw(root)
        result = review.run(
            {
                "artifacts": {
                    "load_raw_counts": {"adata_path": loaded["adata_path"]},
                    "cellranger_count": {
                        "libraries": [
                            {
                                "filtered_feature_bc_matrix": str(
                                    REAL_OUTS / "filtered_feature_bc_matrix.h5"
                                )
                            }
                        ]
                    },
                },
                "run_dir": str(root / "review"),
                "config": {"force_cells": 2_000},
            }
        )
        assert result["cell_calling_state"] == "resolved"
        comparison = result["evidence"]["vs_cellranger"]
        assert comparison["cellranger_cells"] == 1_218
        assert comparison["added_by_this_selection"] == 782
        # The point of the comparison: the added barcodes are visibly ambient.
        assert comparison["median_umi_of_added"] < 100
        assert any("bypasses the EmptyDrops test" in w for w in result["warnings"])


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

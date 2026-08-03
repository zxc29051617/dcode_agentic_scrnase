"""Tests for `merge_samples`: where per-sample work becomes one object.

Run with `python tests/test_merge_samples.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402
from tests import paths  # noqa: E402

merge = load_skill("merge_samples")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _adata(n_cells=20, genes=None, *, genome=None, barcodes=None):
    import anndata
    import numpy as np
    import scipy.sparse as sp

    genes = genes or [f"GENE{i}" for i in range(10)]
    rng = np.random.default_rng(0)
    adata = anndata.AnnData(
        sp.csr_matrix(rng.poisson(4, size=(n_cells, len(genes))).astype("float32"))
    )
    # Deliberately the SAME barcodes every time: that is what real 10x libraries
    # look like, and what makes disambiguation necessary.
    adata.obs_names = barcodes or [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = genes
    if genome:
        adata.var["genome"] = genome
    return adata


def _write(root: Path, samples: dict):
    out = {}
    for name, adata in samples.items():
        out[name] = matrix_io.write_h5ad(adata, root / f"{name}.h5ad")
    return out


def _run(root: Path, samples: dict, producer="load_filtered_counts"):
    return merge.run(
        {
            "artifacts": {producer: {"adata_paths": _write(root, samples)}},
            "run_dir": str(root / "run"),
            "config": {},
        }
    )


# --- the two silent failures this step exists to stop -----------------------


def test_repeated_barcodes_are_disambiguated_by_sample():
    """`AAACCC-1` is a valid barcode in every 10x library ever made.

    Concatenating without suffixing merges cells from different samples that
    share a barcode, and nothing downstream can tell it happened.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {"A": _adata(20), "B": _adata(20)})
        assert result["errors"] == []
        assert result["n_cells"] == 40, "no cell may be absorbed into another"

        import anndata

        merged = anndata.read_h5ad(result["adata_path"])
        assert merged.n_obs == len(set(merged.obs_names)), "barcodes must be unique"
        assert all("-A" in n or "-B" in n for n in merged.obs_names)


def test_a_mismatched_gene_set_is_refused_rather_than_intersected():
    """`anndata.concat` defaults to an inner join and says nothing about it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(
            root,
            {
                "A": _adata(genes=[f"GENE{i}" for i in range(10)]),
                "B": _adata(genes=[f"OTHER{i}" for i in range(10)]),
            },
        )
    assert result["errors"]
    assert "do not share a gene set" in result["errors"][0]
    assert "intersection" in result["errors"][0]


def test_different_genomes_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(
            root,
            {"A": _adata(genome="GRCh38"), "B": _adata(genome="GRCm39")},
        )
    assert result["errors"]
    assert "different genomes" in result["errors"][0]


# --- the sample label -------------------------------------------------------


def test_every_cell_is_labelled_with_its_sample():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {"A": _adata(20), "B": _adata(30)})

        import anndata

        merged = anndata.read_h5ad(result["adata_path"])
        assert result["sample_key"] == "sample"
        counts = merged.obs["sample"].value_counts().to_dict()
        assert counts == {"A": 20, "B": 30}


def test_a_single_sample_is_still_labelled():
    """Downstream never has to ask how many samples there were."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {"only": _adata(15)})

        import anndata

        merged = anndata.read_h5ad(result["adata_path"])
        assert result["n_samples"] == 1
        assert set(merged.obs["sample"]) == {"only"}
        # One sample keeps its barcodes as they were: nothing to disambiguate.
        assert all(not n.endswith("-only") for n in merged.obs_names)


def test_a_lone_adata_path_is_accepted_as_one_sample():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        written = _write(root, {"x": _adata(12)})
        result = merge.run(
            {
                "artifacts": {"load_filtered_counts": {"adata_path": written["x"]}},
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
    assert result["errors"] == []
    assert result["n_samples"] == 1


# --- reporting --------------------------------------------------------------


def test_wildly_uneven_sample_sizes_are_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, {"big": _adata(500), "small": _adata(20)})
    assert result["errors"] == []
    assert any("more than tenfold" in w for w in result["warnings"])


def test_cell_calling_review_wins_over_the_raw_loader():
    """Its output is the subset of the other, so it is the more specific answer."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subset = _write(root / "subset", {"A": _adata(5)})
        full = _write(root / "full", {"A": _adata(500)})
        result = merge.run(
            {
                "artifacts": {
                    "load_raw_counts": {"adata_paths": full},
                    "cell_calling_review": {"adata_paths": subset},
                },
                "run_dir": str(root / "run"),
                "config": {},
            }
        )
    assert result["n_cells"] == 5
    assert result["producer"] == "cell_calling_review"


def test_nothing_to_merge_is_an_error():
    result = merge.run({"artifacts": {}, "run_dir": ".", "config": {}})
    assert any("nothing to merge" in e for e in result["errors"])


# --- real data --------------------------------------------------------------


def test_two_real_samples_merge_if_counted():
    """pbmc_1k_v2 and pbmc_1k_v3, both counted against the same T2T reference."""
    available = {
        name: paths.COUNT_OUTS / name / "outs" / "filtered_feature_bc_matrix.h5"
        for name in ("pbmc_1k_v2", "pbmc_1k_v3")
    }
    if not all(p.is_file() for p in available.values()):
        raise Skip("both pbmc libraries need counting first (see data/README.md)")

    load = load_skill("load_filtered_counts")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        loaded = load.run(
            {
                "artifacts": {
                    "count_matrix_classify": {
                        "matrix_paths": {k: str(v) for k, v in available.items()}
                    }
                },
                "run_dir": str(root),
                "config": {},
            }
        )
        assert loaded["errors"] == []
        result = merge.run(
            {"artifacts": {"load_filtered_counts": loaded}, "run_dir": str(root), "config": {}}
        )
        assert result["errors"] == []
        assert result["n_samples"] == 2
        # 1,015 + 1,218 against the same 39,048-gene reference.
        assert result["n_cells"] == 2_233
        assert result["n_genes"] == 39_048
        assert result["metrics"]["cells_per_sample"] == {
            "pbmc_1k_v2": 1_015,
            "pbmc_1k_v3": 1_218,
        }


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

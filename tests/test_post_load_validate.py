"""Tests for `post_load_validate`: one shape of AnnData whichever route produced it.

Run with `python tests/test_post_load_validate.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

validate = load_skill("post_load_validate")

REAL_OUTS = (
    Path.home() / ".claude/jobs/d529e0fc/tmp/cr_verify/cellranger_count/pbmc_1k_v3/outs"
)


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _adata(n_cells=50, n_genes=20, *, genome=None, counts=True):
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    values = rng.poisson(5, size=(n_cells, n_genes)).astype("float32")
    if not counts:
        values = np.log1p(values / max(values.sum(), 1) * 1e4)
    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    adata.var["gene_ids"] = [f"ENSG{i:08d}" for i in range(n_genes)]
    if genome:
        adata.var["genome"] = genome
    return adata


def _run(root: Path, adata, *, producer="load_filtered_counts", **config):
    path = root / "in.h5ad"
    matrix_io.write_h5ad(adata, path)
    return validate.run(
        {
            "artifacts": {producer: {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the contract -----------------------------------------------------------


def test_a_clean_matrix_only_needs_the_counts_layer():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), species="human")
        # Inside the block: the written file goes away with the temp directory.
        assert Path(result["adata_path"]).is_file()
    assert result["errors"] == []
    assert result["normalizations"] == ["copied raw counts into layers['counts']"]
    assert result["n_cells"] == 50 and result["n_genes"] == 20


def test_the_counts_layer_survives_for_downstream():
    """Normalisation overwrites X in place; without a copy the originals are gone."""
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), species="human")
        written = anndata.read_h5ad(result["adata_path"])
        assert "counts" in written.layers
        assert written.layers["counts"].sum() == written.X.sum()


def test_an_existing_counts_layer_is_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        adata.layers["counts"] = adata.X.copy()
        result = _run(Path(tmp), adata, species="human")
    assert result["normalizations"] == [], "nothing needed changing"


def test_normalized_data_is_refused_rather_than_analysed():
    """Log-normalised values in a counts pipeline give plots that look fine."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(counts=False), species="human")
    assert result["errors"]
    assert "already been normalised" in result["errors"][0]


def test_negative_values_are_refused():
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        adata.X = adata.X.toarray()
        adata.X[0, 0] = -1
        result = _run(Path(tmp), adata, species="human")
    assert any("negative values" in e for e in result["errors"])


def test_duplicate_names_are_made_unique_and_the_change_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        adata.var_names = ["SAME"] * adata.n_vars
        result = _run(Path(tmp), adata, species="human")
    assert result["errors"] == []
    assert any("duplicate var_names unique" in n for n in result["normalizations"])


def test_empty_barcodes_are_dropped_and_flagged():
    """Cell calling should have removed them; doing it silently would hide that."""
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        adata.X = adata.X.toarray()
        adata.X[:5] = 0
        result = _run(Path(tmp), adata, species="human")
    assert result["n_cells"] == 45
    assert any("dropped 5 barcodes" in n for n in result["normalizations"])
    assert any("should have removed them upstream" in w for w in result["warnings"])


def test_missing_gene_ids_are_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        del adata.var["gene_ids"]
        result = _run(Path(tmp), adata, species="human")
    assert any("no gene ids" in n for n in result["notes"])


# --- the species check the matrix route had nowhere to put ------------------


def test_a_matching_genome_verifies_the_declared_species():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="human")
    assert result["species_verified"] is True
    assert result["genome"] == ["GRCh38"]


def test_a_contradicting_genome_stops_the_run():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="mouse")
    assert result["errors"]
    assert "species mismatch" in result["errors"][0]
    assert "wrong organism" in result["errors"][0]


def test_a_matrix_with_no_genome_is_a_note_not_a_warning():
    """Most public data ships as mtx, which records no genome at all."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(), species="human")
    assert result["warnings"] == [], "this must not stop every mtx run at the gate"
    assert any("does not record which genome" in n for n in result["notes"])


def test_a_barnyard_genome_skips_verification():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata()
        adata.var["genome"] = ["GRCh38"] * 10 + ["GRCm39"] * 10
        result = _run(Path(tmp), adata, species="human")
    assert result["errors"] == []
    assert any("barnyard" in n for n in result["notes"])


def test_a_known_genome_with_no_declared_species_is_worth_stopping_for():
    """Unlike the cases above, this one is actionable: just say what it is."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"))
    assert any("no species declared" in w for w in result["warnings"])


# --- producers --------------------------------------------------------------


def test_cell_calling_review_wins_over_the_raw_loader():
    """Its output is the subset of the other, so it is the more specific answer."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        matrix_io.write_h5ad(_adata(n_cells=10), root / "subset.h5ad")
        matrix_io.write_h5ad(_adata(n_cells=500), root / "raw.h5ad")
        result = validate.run(
            {
                "artifacts": {
                    "load_raw_counts": {"adata_path": str(root / "raw.h5ad")},
                    "cell_calling_review": {"adata_path": str(root / "subset.h5ad")},
                },
                "run_dir": str(root / "run"),
                "config": {"species": "human"},
            }
        )
    assert result["n_cells"] == 10
    assert result["source"]["producer"] == "cell_calling_review"


def test_no_producer_is_an_error():
    result = validate.run({"artifacts": {}, "run_dir": ".", "config": {}})
    assert any("no loader ran before this" in e for e in result["errors"])


# --- real data --------------------------------------------------------------


def test_real_filtered_matrix_standardizes_cleanly():
    source = REAL_OUTS / "filtered_feature_bc_matrix.h5"
    if not source.is_file():
        raise Skip("no real cellranger output on this machine")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = validate.run(
            {
                "artifacts": {"load_filtered_counts": {"adata_path": str(source)}},
                "run_dir": str(root),
                "config": {"species": "human"},
            }
        )
        assert result["errors"] == []
        assert result["warnings"] == []
        assert result["normalizations"] == ["copied raw counts into layers['counts']"], (
            "Cell Ranger output needs no repair beyond keeping the counts reachable"
        )
        assert result["n_cells"] == 1_218
        assert result["species_verified"] is True
        assert result["genome"] == ["T2T_CHM13v2_RefSeqLiftoff_v5_3"]


def test_real_matrix_declared_as_the_wrong_species_is_blocked():
    source = REAL_OUTS / "filtered_feature_bc_matrix.h5"
    if not source.is_file():
        raise Skip("no real cellranger output on this machine")
    with tempfile.TemporaryDirectory() as tmp:
        result = validate.run(
            {
                "artifacts": {"load_filtered_counts": {"adata_path": str(source)}},
                "run_dir": tmp,
                "config": {"species": "mouse"},
            }
        )
    assert any("species mismatch" in e for e in result["errors"])


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

"""Tests for `matrix_preflight`, the matrix route's entry check.

Run with `python tests/test_matrix_preflight.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io, species  # noqa: E402
from src.registry import load_skill  # noqa: E402

preflight = load_skill("matrix_preflight")

REAL_OUTS = (
    Path.home() / ".claude/jobs/d529e0fc/tmp/cr_verify/cellranger_count/pbmc_1k_v3/outs"
)

#: 10x's own public matrices. Third-party files the pipeline did not produce, so
#: they catch assumptions that hold only for output we generated ourselves.
TENX_PUBLIC = Path.home() / "data" / "10x_public"


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _adata(*, gene_ids=None, symbols=None, genome=None, n_cells=40, transposed=False):
    import anndata
    import numpy as np
    import scipy.sparse as sp

    n_genes = len(symbols) if symbols else 30
    rng = np.random.default_rng(0)
    values = sp.csr_matrix(rng.poisson(4, size=(n_cells, n_genes)).astype("float32"))
    adata = anndata.AnnData(values)
    # Real 10x-shaped barcodes: 16 nucleotides, no digits. The orientation check
    # keys on exactly this pattern, so a fixture with digits in it proves nothing.
    def barcode(index: int) -> str:
        letters = "ACGT"
        return "".join(letters[(index >> (2 * k)) & 3] for k in range(16)) + "-1"

    adata.obs_names = [barcode(i) for i in range(n_cells)]
    adata.var_names = symbols or [f"GENE{i}" for i in range(n_genes)]
    if gene_ids:
        adata.var["gene_ids"] = gene_ids
    if genome:
        adata.var["genome"] = genome
    return adata.T.copy() if transposed else adata


def _run(root: Path, adata, **config):
    path = root / "m.h5ad"
    matrix_io.write_h5ad(adata, path)
    return preflight.run(
        {"artifacts": {"ingest_validate": {"matrix_path": str(path)}}, "config": config}
    )


# --- species, from whatever evidence the file carries -----------------------


def test_a_recorded_genome_is_the_strongest_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="human")
    assert result["species_verified"] is True
    assert "recorded genome" in result["species_evidence"]


def test_ensembl_ids_identify_the_species_without_a_genome():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata(gene_ids=[f"ENSMUSG{i:08d}" for i in range(30)])
        result = _run(Path(tmp), adata, species="mouse")
    assert result["species_verified"] is True
    assert result["species_evidence"] == "Ensembl gene ids"


def test_symbol_casing_is_the_last_resort_and_says_so():
    """The T2T RefSeq annotation names genes LOC124900618, defeating the other two."""
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata(symbols=[f"CD{i}E" for i in range(40)])
        result = _run(Path(tmp), adata, species="human")
    assert result["species_verified"] is True
    assert "convention, not a guarantee" in result["species_evidence"]


def test_a_contradicting_species_stops_the_run():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata(gene_ids=[f"ENSG{i:08d}" for i in range(30)])
        result = _run(Path(tmp), adata, species="mouse")
    assert result["errors"]
    assert "species mismatch" in result["errors"][0]
    assert "Ensembl gene ids" in result["errors"][0]


def test_no_evidence_at_all_is_a_note_not_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        # Half uppercase, half title case: neither reaches the 80% the casing
        # rule needs, which is the honest "cannot tell" outcome.
        mixed = [f"ABC{i}" for i in range(20)] + [f"Abc{i}" for i in range(20)]
        adata = _adata(symbols=mixed)
        result = _run(Path(tmp), adata, species="human")
    assert result["errors"] == []
    assert result["warnings"] == [], "an uninformative matrix must not stop the run"
    assert any("no usable species evidence" in n for n in result["notes"])


def test_symbol_casing_needs_enough_symbols_to_mean_anything():
    assert species.identify_from_symbols(["CD3E", "ACTB"]) == set(), "two is not a pattern"


def test_stable_ids_are_not_mistaken_for_uppercase_symbols():
    """ENSMUSG is uppercase because it is an id, not because the organism is human."""
    assert species.identify_from_symbols([f"ENSMUSG{i:08d}" for i in range(50)]) == set()
    assert species.identify_from_symbols([f"LOC{i:06d}" for i in range(50)]) == set()


# --- orientation ------------------------------------------------------------


def test_a_transposed_matrix_is_refused():
    """Barcodes in `var_names` means genes are the rows — the mainline expects cells."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(transposed=True), species="human")
    assert result["errors"]
    assert "looks transposed" in result["errors"][0]
    assert result["orientation"] == "genes x cells"


def test_the_normal_orientation_passes():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="human")
    assert result["orientation"] == "cells x genes"


# --- gene ids ---------------------------------------------------------------


def test_missing_gene_ids_are_flagged_as_unstable():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="human")
    assert result["gene_id_convention"] == "symbols only"
    # A note: no one can add stable ids to a matrix that shipped without them.
    assert any("not unique or stable" in n for n in result["notes"])


def test_the_gene_id_convention_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        adata = _adata(gene_ids=[f"ENSG{i:08d}" for i in range(30)])
        result = _run(Path(tmp), adata, species="human")
    assert result["gene_id_convention"] == "ensembl"


# --- the QC constants both entry steps carry --------------------------------


def test_it_carries_the_same_constants_the_fastq_route_gets():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(genome="GRCh38"), species="human")
    assert result["mito_prefix"] == "MT-"
    assert "HBB" in result["erythroid_genes"]
    assert result["marker_db"] == "human"


# --- failures ---------------------------------------------------------------


def test_an_unreadable_file_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        stray = Path(tmp) / "notes.txt"
        stray.write_text("hello")
        result = preflight.run(
            {"artifacts": {"ingest_validate": {"matrix_path": str(stray)}}, "config": {}}
        )
    assert any("cannot read" in e for e in result["errors"])


def test_a_missing_path_is_an_error():
    result = preflight.run(
        {"artifacts": {"ingest_validate": {"matrix_path": "/nope.h5ad"}}, "config": {}}
    )
    assert any("does not exist" in e for e in result["errors"])


# --- non-gene-expression features -------------------------------------------


def test_antibody_features_are_reported_not_silently_dropped():
    """scanpy's read_10x_h5 defaults to gex_only=True and says nothing.

    Dropping them is right for this pipeline; doing it quietly is not. Found by
    running 10x's own pbmc_1k_protein_v3, where 17 Antibody Capture features
    vanished between the file and the AnnData with no trace.
    """
    import h5py
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cite.h5"
        with h5py.File(path, "w") as handle:
            matrix = handle.create_group("matrix")
            n_genes, n_ab, n_cells = 20, 3, 5
            kinds = [b"Gene Expression"] * n_genes + [b"Antibody Capture"] * n_ab
            features = matrix.create_group("features")
            features.create_dataset("feature_type", data=np.array(kinds))
            features.create_dataset(
                "id", data=np.array([f"F{i}".encode() for i in range(n_genes + n_ab)])
            )
            features.create_dataset(
                "name", data=np.array([f"S{i}".encode() for i in range(n_genes + n_ab)])
            )
            features.create_dataset("genome", data=np.array([b"GRCh38"] * (n_genes + n_ab)))
            matrix.create_dataset(
                "barcodes", data=np.array([f"BC{i}".encode() for i in range(n_cells)])
            )
            matrix.create_dataset("shape", data=np.array([n_genes + n_ab, n_cells]))
            matrix.create_dataset("data", data=np.ones(n_cells, dtype="int32"))
            matrix.create_dataset("indices", data=np.zeros(n_cells, dtype="int64"))
            matrix.create_dataset("indptr", data=np.arange(n_cells + 1, dtype="int64"))

        inventory = matrix_io.feature_types_on_disk(path)

    assert inventory == {"Gene Expression": 20, "Antibody Capture": 3}


def test_the_dropped_features_reach_the_operator_as_a_warning():
    """Unlike missing gene ids, there IS a decision here: wrong pipeline, or not wanted."""
    path = TENX_PUBLIC / "pbmc_1k_protein_v3.h5"
    if not path.is_file():
        raise Skip("pbmc_1k_protein_v3 not downloaded")
    result = preflight.run(
        {
            "artifacts": {"ingest_validate": {"matrix_path": str(path)}},
            "config": {"species": "human"},
        }
    )
    assert result["feature_types"]["Antibody Capture"] == 17
    assert any("Antibody Capture" in w and "dropped" in w for w in result["warnings"])


# --- real data --------------------------------------------------------------


def test_real_mtx_directory_is_identified_from_symbol_casing():
    directory = REAL_OUTS / "filtered_feature_bc_matrix"
    if not directory.is_dir():
        raise Skip("no real cellranger output on this machine")
    result = preflight.run(
        {
            "artifacts": {"ingest_validate": {"matrix_path": str(directory)}},
            "config": {"species": "human"},
        }
    )
    assert result["errors"] == []
    assert result["matrix_format"] == "mtx_dir"
    assert result["orientation"] == "cells x genes"
    assert result["species_verified"] is True
    # An mtx directory records no genome, and T2T RefSeq ids are LOC..., so
    # casing is the only evidence left — and it is enough.
    assert "casing" in result["species_evidence"]


def test_real_10x_h5_is_identified_from_its_recorded_genome():
    path = REAL_OUTS / "filtered_feature_bc_matrix.h5"
    if not path.is_file():
        raise Skip("no real cellranger output on this machine")
    result = preflight.run(
        {
            "artifacts": {"ingest_validate": {"matrix_path": str(path)}},
            "config": {"species": "human"},
        }
    )
    assert result["errors"] == []
    assert "recorded genome" in result["species_evidence"]
    assert "T2T_CHM13v2_RefSeqLiftoff_v5_3" in result["species_evidence"]


def test_10x_public_matrices_all_pass_their_own_species_check():
    """Five third-party matrices: two chemistries, a CITE-seq library, and mouse."""
    cases = {
        "pbmc_1k_v2.h5": ("human", 996),
        "pbmc_10k_v3.h5": ("human", 11_769),
        "PBMC_LT_ChromiumX.h5": ("human", 587),
        "pbmc_1k_protein_v3.h5": ("human", 713),
        "neuron_1k_v3.h5": ("mouse", 1_301),
    }
    available = {n: v for n, v in cases.items() if (TENX_PUBLIC / n).is_file()}
    if not available:
        raise Skip("10x public matrices not downloaded")

    for filename, (declared, n_cells) in available.items():
        result = preflight.run(
            {
                "artifacts": {"ingest_validate": {"matrix_path": str(TENX_PUBLIC / filename)}},
                "config": {"species": declared},
            }
        )
        assert result["errors"] == [], f"{filename}: {result['errors']}"
        assert result["species_verified"] is True, filename
        assert result["metrics"]["n_barcodes"] == n_cells, filename
        assert result["orientation"] == "cells x genes", filename


def test_a_mouse_matrix_declared_as_human_is_blocked():
    """The cross-species guard, on a real mouse dataset rather than a fixture."""
    path = TENX_PUBLIC / "neuron_1k_v3.h5"
    if not path.is_file():
        raise Skip("neuron_1k_v3 not downloaded")
    result = preflight.run(
        {
            "artifacts": {"ingest_validate": {"matrix_path": str(path)}},
            "config": {"species": "human"},
        }
    )
    assert any("species mismatch" in e and "mm10" in e for e in result["errors"])


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

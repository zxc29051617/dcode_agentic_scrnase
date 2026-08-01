"""Throwaway input bundles for tests.

Layouts only — the matrix files are empty because `ingest_validate` classifies
from the filesystem and never reads counts. The h5ad fixtures are real files
written by anndata, because that reader does open them.
"""

from __future__ import annotations

from pathlib import Path

TENX_FILES = ("matrix.mtx.gz", "barcodes.tsv.gz", "features.tsv.gz")


def make_mtx_dir(root: Path, name: str = "filtered_feature_bc_matrix") -> Path:
    """A 10x MTX triplet. `name` carries the raw/filtered signal."""
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    for file in TENX_FILES:
        (directory / file).touch()
    return directory


def make_cellranger_outs(root: Path) -> Path:
    """An `outs/` directory holding both matrices, the way Cell Ranger writes it."""
    outs = Path(root) / "outs"
    outs.mkdir(parents=True, exist_ok=True)
    make_mtx_dir(outs, "raw_feature_bc_matrix")
    make_mtx_dir(outs, "filtered_feature_bc_matrix")
    return outs


def make_fastq_dir(
    root: Path,
    samples: tuple[str, ...] = ("SampleA",),
    *,
    reads: tuple[str, ...] = ("R1", "R2"),
    lanes: tuple[str, ...] = ("001",),
    name: str = "fastq",
) -> Path:
    """Illumina-named FASTQs, one file per sample x lane x read."""
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(samples, start=1):
        for lane in lanes:
            for read in reads:
                (directory / f"{sample}_S{index}_L{lane}_{read}_001.fastq.gz").touch()
    return directory


def make_reference(
    root: Path,
    name: str = "GRCh38-ref",
    *,
    genomes: list[str] | None = None,
    version: str | None = None,
) -> Path:
    """A minimal directory that passes for a Cell Ranger reference.

    `genomes` is what mkref stamps in and what every counted matrix carries, so
    it is the field `resolve_reference` cross-checks the species against.
    """
    import json

    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {"genomes": genomes or [name]}
    if version:
        meta["version"] = version
    (directory / "reference.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def write_fastq_record(path: Path, sequence: str, *, n_records: int = 1) -> None:
    """A real (small) gzip FASTQ file, so read-length peeking has something to read."""
    import gzip

    record = f"@read\n{sequence}\n+\n{'F' * len(sequence)}\n" * n_records
    with gzip.open(path, "wt") as handle:
        handle.write(record)


def make_fastq_dir_with_reads(
    root: Path,
    sample: str = "SampleA",
    *,
    r1_length: int = 28,
    r2_length: int = 91,
    lanes: tuple[str, ...] = ("001",),
    name: str = "fastq",
) -> Path:
    """FASTQs with real (small) read content, for fastq_preflight's length checks."""
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    for index, lane in enumerate(lanes, start=1):
        write_fastq_record(directory / f"{sample}_S1_L{lane}_R1_001.fastq.gz", "A" * r1_length)
        write_fastq_record(directory / f"{sample}_S1_L{lane}_R2_001.fastq.gz", "C" * r2_length)
        write_fastq_record(directory / f"{sample}_S1_L{lane}_I1_001.fastq.gz", "G" * 8)
    return directory


def make_h5ad(root: Path, *, n_obs: int = 500, name: str = "data.h5ad") -> Path:
    import anndata
    import numpy as np

    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    adata = anndata.AnnData(np.zeros((n_obs, 3), dtype="float32"))
    adata.write_h5ad(path)
    return path


def bundle_for(config: dict, root: Path) -> Path:
    """Build the bundle a graph test's config implies."""
    if config.get("input_type") == "fastq":
        return make_fastq_dir(root)
    kind = config.get("matrix_kind", "filtered")
    if kind == "raw":
        return make_mtx_dir(root, "raw_feature_bc_matrix")
    if kind == "unknown":
        return make_mtx_dir(root, "counts")
    return make_mtx_dir(root, "filtered_feature_bc_matrix")

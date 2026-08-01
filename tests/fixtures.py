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


def make_10x_fastq_trio(
    root: Path,
    sample: str = "S",
    *,
    n_reads: int = 500,
    r2_quality: int = 37,
    name: str = "fastq",
) -> Path:
    """A 10x-shaped R1/R2/I1 trio with real, varied quality strings.

    The quality characters must span a realistic range: FastQC guesses the
    encoding from the characters it sees, and a file whose quality is one
    repeated high character is read as Illumina 1.5 (offset 64), turning Q37
    into Q6. Real data never looks like that; a fixture must not either.
    """
    import gzip
    import random

    rng = random.Random(0)
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)

    def write(filename: str, length: int, quality: int) -> None:
        with gzip.open(directory / filename, "wt") as handle:
            for i in range(n_reads):
                seq = "".join(rng.choice("ACGT") for _ in range(length))
                quals = [max(2, min(41, quality + rng.randint(-2, 2))) for _ in range(length)]
                # A few reads carry one very low base. That is enough for the
                # character range to force Sanger (offset 33) detection, without
                # dragging any position's mean down far enough to fail a module.
                if i % 100 == 0:
                    quals[0] = 2
                handle.write(f"@r{i}\n{seq}\n+\n" + "".join(chr(33 + q) for q in quals) + "\n")

    write(f"{sample}_S1_L001_R1_001.fastq.gz", 28, 37)
    write(f"{sample}_S1_L001_R2_001.fastq.gz", 91, r2_quality)
    write(f"{sample}_S1_L001_I1_001.fastq.gz", 8, 37)
    return directory


def make_count_matrix_h5(path: Path, genome: str = "GRCh38") -> Path:
    """A 10x-shaped filtered matrix carrying the genome it was counted against.

    Only `matrix/features/genome` matters here — that is the field
    `cellranger_count.assert_same_reference` reads to catch a stale reuse.
    """
    import h5py
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        features = handle.create_group("matrix/features")
        features.create_dataset("genome", data=np.array([genome.encode()] * 3))
    return path


def make_cellranger_outs_h5(
    work: Path, library_id: str = "SampleA", genome: str = "GRCh38"
) -> Path:
    """A finished-looking `<work>/<library>/outs/` with a filtered matrix in it."""
    outs = Path(work) / library_id / "outs"
    outs.mkdir(parents=True, exist_ok=True)
    make_count_matrix_h5(outs / "filtered_feature_bc_matrix.h5", genome)
    (outs / "metrics_summary.csv").write_text(
        "Estimated Number of Cells,Mean Reads per Cell\n1222,42000\n", encoding="utf-8"
    )
    return outs


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

"""Throwaway input bundles for tests.

Layouts only — the matrix files are empty because `ingest_validate` classifies
from the filesystem and never reads counts. The h5ad fixtures are real files
written by anndata, because that reader does open them.
"""

from __future__ import annotations

from pathlib import Path

TENX_FILES = ("matrix.mtx.gz", "barcodes.tsv.gz", "features.tsv.gz")


def make_mtx_dir(
    root: Path,
    name: str = "filtered_feature_bc_matrix",
    *,
    n_barcodes: int | None = None,
    nnz: int | None = None,
    n_features: int = 100,
) -> Path:
    """A 10x MTX triplet with real headers, so it can actually be classified.

    Defaults are chosen from `name` to match what the name claims:
      `*raw*`      -> a barcode list far too long to be called cells
      `*filtered*` -> a plausible cell count with entries for every barcode
      anything else -> the middle range, where the count alone cannot decide

    `count_matrix_classify` reads the barcode file and the MatrixMarket header,
    so empty placeholder files would make it fail rather than classify.
    """
    import gzip

    lowered = name.lower()
    if n_barcodes is None:
        if "raw" in lowered:
            # Small, but with empty droplets — which is what marks a matrix raw,
            # so the fixture does not need the 300,000 barcodes of a real one.
            n_barcodes = 5_000
        elif "filtered" in lowered:
            n_barcodes = 1_000
        else:
            # The middle range, where neither emptiness nor count can decide.
            n_barcodes = 75_000
    if nnz is None:
        nnz = n_barcodes // 2 if "raw" in lowered else n_barcodes * 3

    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    with gzip.open(directory / "barcodes.tsv.gz", "wt") as handle:
        handle.writelines(f"BC{i:08d}-1\n" for i in range(n_barcodes))
    with gzip.open(directory / "features.tsv.gz", "wt") as handle:
        # Genes 0 and 1 are named like a real mitochondrial and erythroid gene,
        # so run_qc_metrics can compute both fractions on this fixture instead of
        # warning that neither was found — a fixture-naming gap, not a real one.
        symbols = ["MT-CO1", "HBB"] + [f"SYM{i}" for i in range(2, n_features)]
        handle.writelines(
            f"ENSG{i:08d}\t{symbols[i]}\tGene Expression\n" for i in range(n_features)
        )

    # Real entries, not just a header: the loaders read these files with scanpy,
    # so a header-only matrix fails rather than classifying.
    #
    # The counts vary between barcodes on purpose. An earlier version gave every
    # barcode the same genes at the same values, which classifies and loads fine
    # but has zero variance — so Scrublet's HVG selection returned no genes and
    # its PCA failed. A fixture that cannot be analysed is not a fixture.
    import random

    rng = random.Random(0)
    non_empty = min(nnz, n_barcodes)
    per_barcode = max(1, nnz // non_empty)
    with gzip.open(directory / "matrix.mtx.gz", "wt") as handle:
        handle.write("%%MatrixMarket matrix coordinate integer general\n%\n")
        handle.write(f"{n_features} {n_barcodes} {nnz}\n")
        written = 0
        for barcode in range(1, non_empty + 1):
            # Two populations loading different halves of the gene space, so
            # there is structure for a neighbour graph to find.
            shift = 0 if barcode % 2 else n_features // 2
            for offset in range(per_barcode):
                if written >= nnz:
                    break
                gene = ((offset + shift) % n_features) + 1
                handle.write(f"{gene} {barcode} {rng.randint(1, 30)}\n")
                written += 1
        # Any remainder goes to the first barcode, on genes it does not have yet.
        gene = per_barcode
        while written < nnz and gene < n_features:
            handle.write(f"{gene + 1} 1 {rng.randint(1, 30)}\n")
            written += 1
            gene += 1
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


#: A whitelist the repository owns, so a bundle that claims to carry
#: recognisable 10x reads does not depend on a Cell Ranger install being present.
#: Pass it to `fastq_preflight` as `config.barcode_whitelist_dir`; see
#: `tests/whitelists/README.md` for why the filename inside matters.
SYNTHETIC_WHITELIST_DIR = Path(__file__).resolve().parent / "whitelists"
SYNTHETIC_WHITELIST = SYNTHETIC_WHITELIST_DIR / "737K-august-2016.txt"


def synthetic_barcodes(n: int | None = None) -> list[str]:
    """The barcodes `make_10x_fastq_trio` writes into R1.

    Raises rather than returning None. The previous version read Cell Ranger's
    own list when it happened to be installed and silently fell back to random
    ACGT when it was not — and random ACGT is correctly reported as "not 10x
    data", so on a machine without Cell Ranger two graph tests failed for a
    reason that had nothing to do with the code under test. A fixture that
    cannot keep its promise has to say so, not quietly keep a weaker one.
    """
    if not SYNTHETIC_WHITELIST.exists():
        raise FileNotFoundError(
            f"{SYNTHETIC_WHITELIST} is missing; it is committed to this repository "
            f"because the FASTQ fixtures cannot produce identifiable 10x reads without it"
        )
    barcodes = [
        line.strip()
        for line in SYNTHETIC_WHITELIST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not barcodes:
        raise ValueError(f"{SYNTHETIC_WHITELIST} is empty")
    return barcodes if n is None else barcodes[:n]


def make_10x_fastq_trio(
    root: Path,
    sample: str = "S",
    *,
    n_reads: int = 500,
    r2_quality: int = 37,
    name: str = "fastq",
) -> Path:
    """A 10x-shaped R1/R2/I1 trio whose barcodes are on a whitelist.

    R1 carries a barcode from `tests/whitelists/737K-august-2016.txt` plus a
    random UMI, so `fastq_preflight` can identify a chemistry — pass
    `SYNTHETIC_WHITELIST_DIR` as `config.barcode_whitelist_dir` and it will.
    This never consults a Cell Ranger install and never degrades to random
    barcodes; if the whitelist is missing it raises.

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
    whitelist = synthetic_barcodes()

    def write(filename: str, length: int, quality: int) -> None:
        is_r1 = "_R1_" in filename
        with gzip.open(directory / filename, "wt") as handle:
            for i in range(n_reads):
                if is_r1:
                    # A whitelisted barcode plus a random UMI, so chemistry
                    # detection has something to recognise.
                    umi = "".join(rng.choice("ACGT") for _ in range(length - 16))
                    seq = whitelist[i % len(whitelist)] + umi
                else:
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
    """Build the bundle a graph test's config implies.

    The FASTQ bundle carries real reads because `fastq_qc` runs actual FastQC on
    it. Cell Ranger is still beyond what a fixture can satisfy, so the FASTQ
    route's graph tests assert routing rather than a completed count.
    """
    if config.get("input_type") == "fastq":
        return make_10x_fastq_trio(root, n_reads=200)
    kind = config.get("matrix_kind", "filtered")
    if kind == "raw":
        return make_mtx_dir(root, "raw_feature_bc_matrix")
    if kind == "unknown":
        return make_mtx_dir(root, "counts")
    return make_mtx_dir(root, "filtered_feature_bc_matrix")

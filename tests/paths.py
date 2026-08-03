"""Where the test data lives.

All of it is project-relative. The bytes are gitignored — 27 GB will not fit in
a repository — so every test that needs them skips when they are absent, and
`scripts/get_test_data.sh` puts them back.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA = PROJECT_ROOT / "data"
REFERENCE = PROJECT_ROOT / "reference"

#: 10x's own public matrices — third-party files this pipeline did not produce.
TENX_PUBLIC = DATA / "10x_public"

#: FASTQ bundles, by dataset name.
FASTQ_BUNDLES = {
    "pbmc_1k_v3": DATA / "pbmc_1k_v3" / "pbmc_1k_v3_fastqs",
    "pbmc_1k_v2": DATA / "pbmc_1k_v2" / "pbmc_1k_v2_fastqs",
    "neuron_1k_v3": DATA / "neuron_1k_v3" / "neuron_1k_v3_fastqs",
}

#: Cell Ranger output from the verification runs, when one has been done.
#: Produced by the commands in data/README.md, not by the test suite.
COUNT_OUTS = DATA / "counted"

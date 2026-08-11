# Rebuilding this environment

The analysis is only as reproducible as the software that produced it. This
project already has the case study: `harmonypy` changed the orientation of the
matrix it returns between 0.0.10 and 0.1.0, scanpy's wrapper assumed the old
one, and the result was a shape error on data that was fine. A version that
moves quietly is a number that moves quietly.

So there are two files, and they answer different questions.

| | |
|---|---|
| `environment.yml` | what the project *asks for*, pinned to the versions it was tested with |
| `conda-lock.yml` | what that resolved to, transitive dependencies and checksums included |

Install from the lock, not from `environment.yml`, unless you are deliberately
re-solving.

## Building it

```bash
pip install conda-lock
conda-lock install --name dcode-scrna conda-lock.yml
conda activate dcode-scrna
python tests/run_all.py
```

`conda-lock` needs a conda, mamba or micromamba to drive; pass `--micromamba`
and it fetches one itself, which is what CI does.

## Platforms

`conda-lock.yml` is solved for **linux-64 only**. That is the platform this runs
on and the platform CI runs on, and a lock that claims platforms nobody has
tested is a claim rather than a guarantee. `conda-lock install` validates the
platform before it installs, so an attempt on macOS or Windows fails with a
clear message instead of silently resolving something else.

To add a platform, re-solve for it and run the suite there before believing it:

```bash
conda-lock lock --file environment.yml --platform linux-64 --platform osx-arm64
```

## Changing a dependency

Edit `environment.yml`, then re-solve:

```bash
conda-lock lock --file environment.yml --platform linux-64
```

Both files are committed together. CI re-runs the solve with
`--check-input-hash`, which re-solves only when `environment.yml` actually
changed, and fails if the committed lock does not match what it produces — so a
pin edited without a re-lock is caught rather than shipped.

## What the lock does *not* cover

Locking these would be pretending. None of them come from a conda channel, and
each has to be installed and recorded separately.

| tool | why it is outside | where it is recorded |
|---|---|---|
| **Cell Ranger** | 10x requires accepting a licence, and the download URL is signed and expires. It cannot be scripted. | `tools/README.md`; the exact version used by a run is in `run_metadata.json` |
| **Reference transcriptomes** | 20–32 GB per species, machine-local | `reference/README.md`; `resolve_reference` records the path and verifies the species |
| **Test datasets** | tens of GB of public 10x data | `data/README.md`; `scripts/get_test_data.sh` |
| **The judge's model** | served by an endpoint, not installed here | `docs/judge_setup.md`; each run records backend, model and prompt hashes in `run_metadata.json` under `judge_sessions` |

The suite runs without any of them. Cell Ranger's own tests use fixtures and a
fake binary; the tests that need real data skip with a reason naming what is
missing, which is why CI can pass on a machine that has none of it.

## What is pinned, and why those

Everything that can change a number in the output:

- **the interpreter** — `python 3.11.15`
- **numerical core** — `numpy`, `scipy`, `scikit-learn`, `pandas`, `h5py`
- **single-cell stack** — `scanpy`, `anndata`, `scikit-misc`, `harmonypy`,
  `scrublet`, `celltypist`
- **graph clustering** — `igraph` (the C library), `python-igraph` (the binding),
  `leidenalg`, `umap-learn`
- **orchestration and serialization** — `langgraph`, `langgraph-checkpoint`,
  `langgraph-checkpoint-sqlite`, `pydantic`
- **the judge's client** — `langchain`, `langchain-core`, `langchain-openai`,
  `openai`
- **sequencing QC** — `fastqc`, `multiqc`, which the FASTQ route shells out to

`run_metadata.json` records the installed version of most of these at run start
as well, because the lock says what *should* be installed and the metadata says
what *was*.

### Some scientific packages are pinned on the pip side

`scanpy`, `anndata`, `pandas` and `scikit-misc` were declared as conda
dependencies but resolved from PyPI: `celltypist` and `scrublet` pull them in
during the pip pass, and those builds are what ended up installed. The conda
pins described an environment that was never the one running. They are now
declared once, on the side that wins.

This also means **`micromamba -f conda-lock.yml` is not enough** — measured, not
assumed: it installs the 257 conda packages and silently skips all 55 pip ones,
leaving an environment with no `langgraph` and no `scanpy`. Use `conda-lock
install`, which does both halves.

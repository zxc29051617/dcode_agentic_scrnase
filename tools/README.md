# tools/

Third-party binaries, as symlinks. **Nothing here is in the repository.**

```
tools/
  cellranger-10.1.0 -> wherever it was unpacked
  cellranger        -> cellranger-10.1.0/bin/cellranger
```

## Cell Ranger

It cannot be scripted: 10x requires accepting a licence, and the download URL
is signed and expires. Get it from
<https://www.10xgenomics.com/support/software/cell-ranger/downloads>, unpack it
anywhere, then point the project at it:

```bash
ln -sfn /path/to/cellranger-10.1.0 tools/cellranger-10.1.0
ln -sfn cellranger-10.1.0/bin/cellranger tools/cellranger
```

The symlink is a convenience, not a requirement — `cellranger_count` finds the
binary on `PATH` or in the usual unpack locations (`~/projects/cellranger-*`,
`/opt/cellranger-*`) on its own. Set `config.binary` to override.

`fastq_preflight` also reads the barcode whitelists that ship inside the
install, at `lib/python/cellranger/barcodes/`, to identify chemistry. Without
Cell Ranger it says so and carries on rather than guessing.

## Everything else

FastQC and MultiQC come from conda and are in `environment.yml`:

```bash
conda env update -f environment.yml
```

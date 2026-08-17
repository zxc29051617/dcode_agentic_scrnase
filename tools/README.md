# tools/

Third-party binaries. **Nothing here is in the repository** — `.gitignore`
keeps everything but this file out.

```
tools/
  cellranger-10.1.0/   unpacked here, a real directory
  cellranger        -> cellranger-10.1.0/bin/cellranger
```

Unpack it **here**, rather than elsewhere with a symlink pointing in. A
symlink to a path outside the project is a path something else can delete: on
2026-08-17 the install under `~/projects/` was removed while `mkref` was
running, and the build only survived because Linux keeps a deleted-but-open
file readable — the next step to `exec()` a new subprocess failed outright.
Everything the pipeline needs to find Cell Ranger now checks `tools/` first
for that reason.

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

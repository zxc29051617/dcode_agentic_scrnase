# Synthetic barcode whitelist, for tests only

`737K-august-2016.txt` here is **not** 10x's whitelist. It is 96 invented 16-mers
that exist so the FASTQ tests can run on a machine with no Cell Ranger.

## Why it has that filename

`fastq_preflight` identifies chemistry by *which* whitelist the barcodes belong
to, and the mapping is by filename — `737K-august-2016.txt` means 3' v2 or 5'.
A file called anything else would be read and then matched to no chemistry, so
the name is load-bearing rather than decorative.

## Why it exists at all

`tests/fixtures.py` used to read the real list out of a Cell Ranger install
found by globbing `~/projects/cellranger-*`, and fall back to random barcodes
when there wasn't one. Random ACGT is correctly reported as "not 10x data", so
on a machine without Cell Ranger two graph tests failed — and they failed for a
reason that had nothing to do with the code under test. CI found this the first
time it ran; a developer with Cell Ranger installed never would.

Now the fixture always draws from this file and `make_10x_fastq_trio` raises if
it is missing, so a bundle that claims to carry recognisable 10x reads either
does or the test errors saying why.

## How it was generated

96 unique 16-mers over ACGT from `random.Random(0)`, sorted. Regenerating it
needs the fixture regenerated too — they only work as a pair, and
`tests/test_fastq_whitelist.py` checks that every barcode the fixture writes is
on this list.

96 is small on purpose: enough that `_sample_barcodes` sees a hit rate of 1.0
against `WHITELIST_HIT_THRESHOLD = 0.5`, few enough to read.

## Production is unaffected

Nothing outside `tests/` points at this directory. Tests pass it explicitly as
`config.barcode_whitelist_dir`; with that key unset — which is every real run —
`fastq_preflight` looks where Cell Ranger actually installs its whitelists, as
it always has.

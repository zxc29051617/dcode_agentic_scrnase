"""Fetch the scMayoMap marker database and convert it to plain text, once.

`cross_check_annotation` reads `marker_db/scmayomap/markers.csv`, which is committed.
This script is how that file was produced, and how to reproduce it if the
upstream database is ever updated. **The pipeline never runs this**, which is
the point: reading an `.rda` needs `pyreadr`, and a cross-check that is only
advisory should not put a binary-format reader on the critical path of every
install.

The conversion is lossless for what the scoring uses. The upstream object is a
21,658 x 606 wide matrix that is almost entirely zeros; the same content in long
form is one row per (tissue, cell_type, gene) that is actually a marker.

    pip install pyreadr        # only to run this script
    python scripts/fetch_scmayomap_db.py

Source: https://github.com/chloelulu/scMayoMap (MIT)
Paper:  BMC Biology 2023, https://doi.org/10.1186/s12915-023-01728-6
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFS = PROJECT_ROOT / "marker_db" / "scmayomap"
RDA_URL = "https://raw.githubusercontent.com/chloelulu/scMayoMap/main/data/scMayoMapDatabase.rda"
LICENSE_URL = "https://raw.githubusercontent.com/chloelulu/scMayoMap/main/LICENSE"


def main() -> int:
    try:
        import pandas as pd
        import pyreadr
    except ImportError as exc:
        print(f"needs pyreadr and pandas to run: {exc}\n  pip install pyreadr", file=sys.stderr)
        return 1

    REFS.mkdir(parents=True, exist_ok=True)
    rda = REFS / "scMayoMapDatabase.rda"

    print(f"fetching {RDA_URL}")
    urllib.request.urlretrieve(RDA_URL, rda)
    digest = hashlib.sha256(rda.read_bytes()).hexdigest()
    print(f"  {rda.stat().st_size:,} bytes  sha256={digest[:16]}...")

    wide = pyreadr.read_r(str(rda))["scMayoMapDatabase"]
    print(f"  wide form: {wide.shape[0]:,} x {wide.shape[1]:,}")

    # tissue and cell type are fused in the column names as "tissue:cell type".
    # Rows are already restricted to one tissue, so a column belonging to another
    # tissue is all zeros on those rows and carries nothing.
    value_cols = [c for c in wide.columns if c not in ("tissue", "gene")]
    long = wide.melt(id_vars=["tissue", "gene"], value_vars=value_cols,
                     var_name="column", value_name="is_marker")
    long = long[long["is_marker"] > 0].copy()
    long["column_tissue"] = long["column"].str.split(":", n=1).str[0]
    crossed = int((long["column_tissue"] != long["tissue"]).sum())
    if crossed:
        print(f"  note: {crossed} markers sit in another tissue's column; keeping the column's")

    out = pd.DataFrame({
        "tissue": long["column"].str.split(":", n=1).str[0],
        "cell_type": long["column"].str.split(":", n=1).str[1],
        "gene": long["gene"].str.upper(),
    }).drop_duplicates().sort_values(["tissue", "cell_type", "gene"], ignore_index=True)

    markers = REFS / "markers.csv"
    out.to_csv(markers, index=False)
    print(f"  long form: {len(out):,} markers, "
          f"{out['tissue'].nunique()} tissues, "
          f"{out.groupby(['tissue', 'cell_type']).ngroups} tissue/cell-type pairs")

    try:
        urllib.request.urlretrieve(LICENSE_URL, REFS / "LICENSE")
    except Exception as exc:                                  # noqa: BLE001
        print(f"  could not fetch LICENSE: {exc}", file=sys.stderr)

    (REFS / "PROVENANCE.json").write_text(json.dumps({
        "name": "scMayoMapDatabase",
        "source_url": RDA_URL,
        "source_sha256": digest,
        "upstream_repo": "https://github.com/chloelulu/scMayoMap",
        "upstream_version": "0.2.0",
        "license": "MIT",
        "citation": (
            "Single-cell Mayo Map (scMayoMap): an easy-to-use tool for cell type "
            "annotation in single-cell RNA-sequencing data analysis. BMC Biology, 2023. "
            "https://doi.org/10.1186/s12915-023-01728-6"
        ),
        "converted_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "converted_by": "scripts/fetch_scmayomap_db.py",
        "markers": len(out),
        "tissues": sorted(out["tissue"].unique()),
        "markers_sha256": hashlib.sha256(markers.read_bytes()).hexdigest(),
    }, indent=2) + "\n")

    rda.unlink()          # the CSV is the artifact; the .rda was the transport
    print(f"\nwrote {markers.relative_to(PROJECT_ROOT)} "
          f"({markers.stat().st_size / 1024:.0f} KB) and PROVENANCE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for `detect_doublets`.

Scrublet is slow enough that the fixtures here are deliberately small and few:
the behaviour worth pinning is per-library scoring, the expected rate, and the
refusals — not Scrublet's own arithmetic, which is not ours to test.

Run with `python tests/test_detect_doublets.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import matrix_io  # noqa: E402
from src.registry import load_skill  # noqa: E402

dbl = load_skill("detect_doublets")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _adata(n_cells=200, n_genes=200, *, samples=None):
    """Structured enough for Scrublet to fit: two populations plus noise.

    The gene count is generous on purpose. Scrublet does its own HVG selection
    before a 30-component PCA, so a fixture with only a few dozen genes leaves
    fewer components than it asks for and the fit fails for reasons that have
    nothing to do with this step.
    """
    import anndata
    import numpy as np
    import scipy.sparse as sp

    rng = np.random.default_rng(0)
    half = n_cells // 2
    a = rng.poisson(3, size=(half, n_genes))
    b = rng.poisson(3, size=(n_cells - half, n_genes))
    a[:, : n_genes // 2] += 8          # one population loads the first genes
    b[:, n_genes // 2 :] += 8          # the other loads the rest
    values = np.vstack([a, b]).astype("float32")

    adata = anndata.AnnData(sp.csr_matrix(values))
    adata.obs_names = [f"BC{i:04d}-1" for i in range(n_cells)]
    adata.var_names = [f"GENE{i}" for i in range(n_genes)]
    if samples:
        adata.obs["sample"] = [samples[i % len(samples)] for i in range(n_cells)]
    return adata


def _run(root: Path, adata, **config):
    path = matrix_io.write_h5ad(adata, root / "in.h5ad")
    return dbl.run(
        {
            "artifacts": {"apply_cell_qc_filter": {"adata_path": str(path)}},
            "run_dir": str(root / "run"),
            "config": config,
        }
    )


# --- the expected rate ------------------------------------------------------


def test_expected_rate_follows_the_10x_loading_table():
    """Scrublet's own 0.06 default assumes a recovery of about 8,000 cells."""
    assert dbl.expected_rate_for(1_000) == 0.0076
    assert dbl.expected_rate_for(10_000) == 0.076, "10x publishes 7.6% at 10,000"
    # A small library must not be searched at the default 6%.
    assert dbl.expected_rate_for(1_218) < 0.01


def test_the_rate_is_capped_rather_than_extrapolated_forever():
    assert dbl.expected_rate_for(1_000_000) <= 0.25


def test_the_rate_is_derived_per_library_and_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200))
    entry = next(iter(result["per_sample"].values()))
    assert entry["expected_rate_source"] == "10x loading table"
    assert entry["expected_rate"] == dbl.expected_rate_for(entry["n_cells"])


def test_a_configured_rate_wins_and_is_recorded_as_such():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200), expected_doublet_rate=0.05)
    entry = next(iter(result["per_sample"].values()))
    assert entry["expected_rate"] == 0.05
    assert entry["expected_rate_source"] == "config"


# --- per library ------------------------------------------------------------


def test_each_library_is_scored_on_its_own():
    """Doublets form in a GEM well; simulating them across libraries is meaningless."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, samples=["A", "B"]))
    assert result["errors"] == []
    assert set(result["per_sample"]) == {"A", "B"}
    assert all(e["assessed"] for e in result["per_sample"].values())
    # Each got its own threshold from its own simulated doublets.
    assert all(e["threshold_used"] is not None for e in result["per_sample"].values())


def test_per_sample_rates_are_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(
            Path(tmp), _adata(200, samples=["A", "B"]),
            expected_doublet_rate={"A": 0.05, "B": 0.01},
        )
    assert result["per_sample"]["A"]["expected_rate"] == 0.05
    assert result["per_sample"]["B"]["expected_rate"] == 0.01


def test_a_shallow_library_is_still_scored_rather_than_crashing():
    """Scrublet's fixed 30 components are more than a small matrix can supply.

    A library with few genes leaves fewer than 30 after Scrublet's own HVG
    selection, and arpack raises rather than returning a smaller basis. The
    library is real and has enough cells, so the answer is a smaller PCA — not
    a stack trace turned into "not assessed".
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200, n_genes=60))
    entry = next(iter(result["per_sample"].values()))
    assert entry["assessed"] is True, result["warnings"]
    assert result["errors"] == []


def test_components_stay_at_scrublets_default_when_the_data_allows():
    """The bound only ever lowers; a normal library is scored exactly as before."""
    assert dbl._components_for(_adata(2_000, n_genes=2_000)) == dbl.SCRUBLET_COMPONENTS


def test_a_library_too_small_to_score_is_marked_not_assessed():
    """A number nobody should trust is worse than an honest gap."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(20))
    entry = next(iter(result["per_sample"].values()))
    assert entry["assessed"] is False
    assert any("too few for Scrublet" in w for w in result["warnings"])
    # The run still continues with an unscored column rather than failing.
    assert result["errors"] == []
    assert result["adata_path"] is not None


# --- annotating vs removing --------------------------------------------------


def test_doublets_are_annotated_but_not_removed_by_default():
    import anndata

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run(root, _adata(200))
        written = anndata.read_h5ad(result["adata_path"])
        assert {"doublet_score", "predicted_doublet", "doublet_assessed"} <= set(written.obs)
        assert written.n_obs == 200, "nothing is removed without being asked"
    assert result["doublets_removed"] is False


def test_remaining_doublets_are_said_out_loud():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200), doublet_threshold=0.0)
    assert result["doublets_removed"] is False
    assert any("remain in the data" in n for n in result["notes"])


def test_removal_happens_when_asked():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        annotated = _run(root, _adata(200))
        removed = _run(root, _adata(200), remove_doublets=True)
    summary = removed["doublet_summary"]
    assert removed["doublets_removed"] is True
    assert summary["n_cells_out"] == 200 - summary["n_doublets"]
    assert annotated["doublet_summary"]["n_cells_out"] == 200


def test_removing_every_cell_is_refused():
    """A threshold that calls everything is a failed fit, not a finding."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200), remove_doublets=True, doublet_threshold=-1)
    assert result["errors"]
    assert "would leave nothing" in result["errors"][0]


def test_an_implausible_doublet_fraction_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), _adata(200), doublet_threshold=-1)
    assert any("more likely a failed fit" in w for w in result["warnings"])


# --- failures ----------------------------------------------------------------


def test_missing_path_is_an_error():
    result = dbl.run({"artifacts": {}, "run_dir": "."})
    assert any("apply_cell_qc_filter must run first" in e for e in result["errors"])


def test_nonexistent_path_is_an_error():
    result = dbl.run(
        {"artifacts": {"apply_cell_qc_filter": {"adata_path": "/nope.h5ad"}}, "run_dir": "."}
    )
    assert any("does not exist" in e for e in result["errors"])


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

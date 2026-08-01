"""Run every test module and return a combined exit code.

    python tests/run_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import test_fastq_preflight, test_graph_smoke, test_ingest_validate  # noqa: E402

MODULES = (test_ingest_validate, test_fastq_preflight, test_graph_smoke)


def main() -> int:
    failed = 0
    for module in MODULES:
        print(f"\n{module.__name__}")
        failed |= module.main()
    print("\nFAILURES" if failed else "\nall suites passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

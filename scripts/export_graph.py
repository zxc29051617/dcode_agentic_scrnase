"""Write `docs/graph.mmd` from the compiled graph.

The diagram is the wiring, not a drawing of it, so it has to come from the same
`build_graph` the runner uses. Regenerate after changing the registry:

    python scripts/export_graph.py            # write docs/graph.mmd
    python scripts/export_graph.py --check    # fail if it is out of date
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.graph import build_graph  # noqa: E402
from src.judge import StubJudge  # noqa: E402

OUTPUT = PROJECT_ROOT / "docs" / "graph.mmd"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the file differs, and write nothing")
    args = parser.parse_args(argv)

    # The judge is a stub because only the shape is being drawn; no step runs.
    graph = build_graph(judge=StubJudge()).get_graph()
    mermaid = graph.draw_mermaid()

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != mermaid:
            print(f"{OUTPUT.relative_to(PROJECT_ROOT)} is out of date; "
                  "run python scripts/export_graph.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT.relative_to(PROJECT_ROOT)} is current")
        return 0

    OUTPUT.write_text(mermaid, encoding="utf-8")
    conditional = sum(1 for edge in graph.edges if edge.conditional)
    print(f"wrote {OUTPUT.relative_to(PROJECT_ROOT)}: "
          f"{len(graph.nodes)} nodes, {len(graph.edges)} edges "
          f"({conditional} conditional)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

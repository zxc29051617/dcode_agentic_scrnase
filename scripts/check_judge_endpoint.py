#!/usr/bin/env python
"""Check the judge endpoint before a run depends on it.

Three things fail differently and are worth telling apart, because the fix for
each is somewhere else entirely:

  unreachable   -> network, or the wrong host from inside a container
  reachable but the model is missing -> `ollama pull` on the server
  model present but no structured output -> a different model

`python -m src.run --judge local` fails at whichever of these is broken, but
several steps into a pipeline and with a traceback from langchain. This says it
in one line, in two seconds, before anything expensive starts.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_URL = "http://lsbnb-dgx2.iis.sinica.edu.tw:11434/v1"
TIMEOUT = 20


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.load(response)


def main() -> int:
    base = os.environ.get("SCRNA_JUDGE_BASE_URL", DEFAULT_URL).rstrip("/")
    want = os.environ.get("SCRNA_JUDGE_MODEL", "gpt-oss:20b")
    print(f"endpoint  {base}")
    print(f"model     {want}")

    try:
        listed = _get(f"{base}/models")
    except (urllib.error.URLError, OSError) as exc:
        print(f"\nFAIL      cannot reach {base}: {exc}")
        print("          from a container, localhost is the container — use the host address")
        return 1

    names = sorted(entry["id"] for entry in listed.get("data", []))
    print(f"\nreachable, {len(names)} models served")
    if want not in names:
        print(f"FAIL      {want!r} is not on this server")
        print("          available: " + ", ".join(names))
        return 1
    print(f"ok        {want} is served")

    # The judge asks for a schema-constrained object. A model that chats happily
    # but ignores the schema produces a verdict that will not validate, which is
    # worth finding out now rather than mid-run.
    from src.judge import LocalLLMJudge

    payload = {
        "step": "smoke_test",
        "status": "ok",
        "warnings": ["a synthetic warning, so a correct judge should not answer `pass`"],
        "errors": [],
        "output": {"n_cells": 1000},
        "metrics": {"n_cells": 1000},
    }
    try:
        verdict = LocalLLMJudge().judge("smoke_test", payload)
    except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
        print(f"FAIL      structured output failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"ok        structured output parsed: verdict={verdict.verdict} score={verdict.score}")
    if verdict.verdict == "pass":
        print("warn      a payload carrying a warning was judged `pass`; check the prompt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

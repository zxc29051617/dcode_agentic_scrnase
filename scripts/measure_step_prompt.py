"""Judge one saved payload with and without a step prompt, and print both.

A prompt nobody measured is a prompt whose effect nobody knows. This holds
everything else still — same payload, same model, same temperature — so the
only thing that differs between the two arms is the file under
`prompts/steps/`.

    python scripts/measure_step_prompt.py run_clustering --run runs/<id>
    python scripts/measure_step_prompt.py --all --run runs/<id> --repeat 3

Needs a reachable judge endpoint: set `SCRNA_JUDGE_BASE_URL` and
`SCRNA_JUDGE_MODEL`, or pass `--base-url` and `--model`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.judge import STEP_PROMPT_DIR, LocalLLMJudge  # noqa: E402
from src.nodes import _judge_view  # noqa: E402
from src.registry import REGISTRY  # noqa: E402


def load_payload(run_dir: Path, step: str) -> dict:
    """The payload the graph would hand the judge for this step."""
    output = json.loads((run_dir / step / "output.json").read_text())
    view, shortened = _judge_view(step, output)
    payload = {
        "step": step,
        "status": "error" if output.get("errors") else "ok",
        "warnings": output.get("warnings") or [],
        "errors": output.get("errors") or [],
        "output": view,
        "metrics": output.get("metrics") or {},
    }
    if shortened:
        payload["output_is_abridged"] = shortened
    return payload


def judge_once(judge: LocalLLMJudge, step: str, payload: dict, *, with_prompt: bool) -> dict:
    # The two arms differ only here: an empty addendum reproduces exactly what a
    # step with no file would get.
    judge._step_prompts = {} if with_prompt else {step: ""}
    verdict = judge.judge(step, payload)
    return verdict.model_dump()


def render(label: str, body: dict) -> None:
    print(f"  [{label}] {body.get('verdict')}  score {body.get('score')}")
    for reason in body.get("reasons") or []:
        print(f"      - {reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("steps", nargs="*", help="steps to measure")
    parser.add_argument("--all", action="store_true",
                        help="every step that has a prompt file")
    parser.add_argument("--run", required=True, help="a completed run directory")
    parser.add_argument("--repeat", type=int, default=1,
                        help="runs per arm; one says nothing about reliability")
    parser.add_argument("--model", default=os.environ.get("SCRNA_JUDGE_MODEL"))
    parser.add_argument("--base-url", default=os.environ.get("SCRNA_JUDGE_BASE_URL"))
    args = parser.parse_args(argv)

    run_dir = Path(args.run).expanduser()
    steps = args.steps
    if args.all:
        steps = sorted(p.stem for p in STEP_PROMPT_DIR.glob("*.md") if p.name != "README.md")
    if not steps:
        parser.error("name a step, or pass --all")

    unknown = [s for s in steps if s not in REGISTRY]
    if unknown:
        parser.error(f"not registry steps: {unknown}")
    missing = [s for s in steps if not (run_dir / s / "output.json").exists()]
    if missing:
        parser.error(f"{run_dir} has no saved output for: {missing}")

    judge = LocalLLMJudge(model=args.model, base_url=args.base_url)
    print(f"model {judge.llm.model_name}   run {run_dir.name}   "
          f"{args.repeat} run(s) per arm\n")

    for step in steps:
        has_prompt = (STEP_PROMPT_DIR / f"{step}.md").exists()
        payload = load_payload(run_dir, step)
        print("=" * 78)
        print(f"{step}   payload {len(json.dumps(payload)):,} chars"
              f"{'' if has_prompt else '   (no prompt file — both arms identical)'}")
        print("=" * 78)

        for attempt in range(1, args.repeat + 1):
            if args.repeat > 1:
                print(f"  --- run {attempt} ---")
            for label, with_prompt in (("before", False), ("after", True)):
                try:
                    render(label, judge_once(judge, step, payload, with_prompt=with_prompt))
                except Exception as exc:                       # noqa: BLE001
                    print(f"  [{label}] failed: {type(exc).__name__}: {exc}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

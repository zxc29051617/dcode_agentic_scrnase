"""Read `.env`, which the documentation has always said this does.

`.env.example` ships with the project, `docs/judge_setup.md` tells you to copy it
to `.env` and set four variables in it, and nothing anywhere read the file. The
variables worked only if you also exported them in your shell — so a person who
followed the instructions exactly got the stub judge and no indication why.

## No new dependency

`python-dotenv` would do this. It would also mean re-solving `conda-lock.yml`,
rebuilding the environment and re-running the suite to prove the result, which
is a lot of moving parts for the thirty lines below. The format this project
actually uses is `KEY=value` with comments — not the shell-substitution,
multiline, interpolating superset — so the parser is small enough to read in
one sitting and to test exhaustively.

## The real environment always wins

An exported variable beats the file. `.env` is where a machine's usual settings
live; an explicit `SCRNA_JUDGE_MODEL=... python -m src.run` is somebody
deliberately doing something different for one run, and a file on disk must not
quietly undo that. CI depends on this too: it sets `SCRNA_JUDGE_BACKEND=stub`
in the job, and a `.env` that arrived some other way must not point it at a
model endpoint.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def candidate_paths(start: Path | None = None) -> list[Path]:
    """The `.env` files that would be considered, in precedence order."""
    working = Path(start) if start is not None else Path.cwd()
    paths = [working / ".env", PROJECT_ROOT / ".env"]
    seen: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen


def parse(text: str) -> dict[str, str]:
    """`KEY=value` lines, minus comments, blanks and one layer of quotes.

    Deliberately not a shell parser. There is no interpolation, no `$VAR`, no
    line continuation and no multiline value: this reads settings files that
    this project writes, and a parser that accepts more than the format it
    documents is a parser that will one day disagree with the documentation.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # A trailing comment is only a comment when it is not inside quotes.
        if value[:1] in {'"', "'"}:
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        elif "#" in value:
            value = value.split("#", 1)[0].strip()
        values[key] = value
    return values


def load(start: Path | None = None) -> tuple[Path | None, list[str]]:
    """Put the first `.env` found into the environment, without overriding it.

    Returns `(path, keys_set)`. `keys_set` names the variables this call
    introduced — the names only. The values are the point of the file being
    gitignored, and this is called from a CLI that prints things.
    """
    for path in candidate_paths(start):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        applied: list[str] = []
        for key, value in parse(text).items():
            if key in os.environ:
                continue
            os.environ[key] = value
            applied.append(key)
        return path, applied
    return None, []

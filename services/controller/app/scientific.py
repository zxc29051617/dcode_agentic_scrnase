"""Finding the scientific package, once, from anywhere this service is started.

Three modules here read something from `src/`: the revisable-parameter
allowlist, the species vocabulary, and the step registry. None of them executes
anything — they are read-only metadata, and borrowing them is what stops this
service growing a second, drifting copy of facts the executor already owns.

They were each importing `src.…` directly, and each had a fallback for when the
import failed, because a deployment without the scientific package on its path
is a real configuration. What none of them had was a way to *succeed* when
started the documented way.

## The failure this fixes

`uvicorn app.main:app` is run from `services/controller/`, so `sys.path` starts
with that directory and the repository root is nowhere on it. Every one of the
three imports raised `ModuleNotFoundError` in the running service, and every
one degraded quietly and plausibly:

- the execution plan reported zero steps and zero gates
- species validation accepted anything, because the known list came back empty
- `revise` with an override was refused with "cannot be validated here"

Only the third was visible, and only because it fails closed. The tests did not
catch any of it: `tests/conftest.py` puts the repository root on `sys.path`
before importing the app, so the suite was exercising an import that production
never had.

So the path is established here, at import time, from this file's own location
— which is true wherever the process was started from — and
`tests/test_scientific_path.py` asserts it without the conftest's help.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: `services/controller/app/scientific.py` → the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]


def ensure_importable() -> bool:
    """Put the repository root on `sys.path` and say whether `src` is reachable.

    Idempotent, and appends rather than prepends: this service's own modules
    must keep winning any name collision with the repository root's, and being
    able to read `src.registry` is not worth shadowing `app.config`.
    """
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.append(root)
    return (REPO_ROOT / "src" / "registry.py").is_file()


#: Run on import, so a module only has to import this one to be able to reach
#: the scientific package.
AVAILABLE = ensure_importable()

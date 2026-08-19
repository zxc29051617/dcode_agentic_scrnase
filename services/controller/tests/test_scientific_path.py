"""The controller must reach the scientific package the way it is actually run.

This file exists because the rest of the suite could not have caught the bug it
guards. `tests/conftest.py` puts the repository root on `sys.path` before
importing the app, so every other test exercised an import that the running
service never had: `uvicorn app.main:app` is started from `services/controller/`,
where the repository root is nowhere on the path.

Three things degraded quietly and plausibly in production as a result — the
execution plan reported zero steps and zero gates, species validation accepted
anything because the known list came back empty, and `revise` with an override
was refused with "cannot be validated here". Only the third was visible, and
only because it fails closed.

So these run in a **subprocess with the service's own working directory and no
help from the fixtures**, which is the only arrangement that reproduces it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _in_service_dir(code: str) -> str:
    """Run `code` the way uvicorn runs the app: from services/controller/."""
    finished = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SERVICE_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert finished.returncode == 0, (
        f"exit {finished.returncode}\n--- stdout ---\n{finished.stdout}"
        f"\n--- stderr ---\n{finished.stderr}"
    )
    return finished.stdout.strip()


def test_the_scientific_package_is_reachable_from_the_service_directory():
    out = _in_service_dir(
        "from app.scientific import ensure_importable;"
        "ensure_importable();"
        "from src.registry import REGISTRY;"
        "print(len(REGISTRY))"
    )
    assert int(out) > 0, "the step registry came back empty"


def test_the_execution_plan_lists_real_steps_when_started_that_way():
    """The symptom a person would have seen: a preview promising nothing."""
    out = _in_service_dir(
        "from app.plan import execution_plan;"
        "p = execution_plan(input_path=None, analysis={}, study_design_ref=None);"
        "print(len(p['steps']), len(p['gates']))"
    )
    steps, gates = (int(x) for x in out.split())
    assert steps > 20, f"the plan listed {steps} steps"
    assert gates > 0, "the plan predicted no human gates at all"


def test_species_validation_has_a_vocabulary_when_started_that_way():
    out = _in_service_dir(
        "from app.validation import known_species, validate_species;"
        "print(len(known_species()));"
        "print(validate_species('tribble')[1] != [])"
    )
    count, refuses = out.splitlines()
    assert int(count) > 0, "no species are known, so every species would be accepted"
    assert refuses == "True", "an invented species was not refused"


def test_an_override_can_actually_be_converted_when_started_that_way():
    """The one that failed closed, and so was the only visible symptom."""
    out = _in_service_dir(
        "from app.main import _coerce;"
        "accepted, rejected = _coerce({'min_genes': '250'}, ['min_genes']);"
        "print(accepted, rejected)"
    )
    assert "250.0" in out, f"the override was not converted: {out}"
    assert "[]" in out, f"the override was rejected: {out}"


def test_the_path_helper_is_idempotent_and_does_not_shadow_the_service():
    """Appended, not prepended: `app.config` must keep beating any module of
    the same name at the repository root."""
    out = _in_service_dir(
        "import sys;"
        "from app.scientific import REPO_ROOT, ensure_importable;"
        "ensure_importable(); ensure_importable();"
        "print(sys.path.count(str(REPO_ROOT)));"
        "print(sys.path.index(str(REPO_ROOT)) > 0)"
    )
    count, appended = out.splitlines()
    assert count == "1", "the repository root was added more than once"
    assert appended == "True", "the repository root was put ahead of the service's own modules"

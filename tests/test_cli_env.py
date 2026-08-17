"""What the command line and `.env` actually resolve to.

Every gap pinned here was documented before it worked:

  - `docs/judge_setup.md` said to put four variables in `.env`. Nothing read the
    file. They worked only if you *also* exported them, so following the
    instructions exactly gave you the stub and no hint why.
  - `--judge openai-compatible` was documented and rejected by argparse.
  - `--judge` defaulted to `"stub"`, so the CLI always passed an explicit value
    and `SCRNA_JUDGE_BACKEND` could never be reached from the command line.
  - There was no way at all to choose the model from the command line.

Run with `python tests/test_cli_env.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import envfile  # noqa: E402
from src.judge import BACKEND_ALIASES, LocalLLMJudge, StubJudge, get_judge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.provenance import AuditLog  # noqa: E402
from src.run import build_parser, run_workflow  # noqa: E402
from tests import fixtures  # noqa: E402

JUDGE_VARS = ("SCRNA_JUDGE_BACKEND", "SCRNA_JUDGE_MODEL",
              "SCRNA_JUDGE_API_KEY", "SCRNA_JUDGE_BASE_URL")

#: Must never reach run_metadata.json, the audit log or a report.
SECRET = "sk-from-dotenv-must-never-be-written-0987"


@contextmanager
def clean_env(**overrides: str):
    """Run with the judge variables under this test's control."""
    saved = {key: os.environ.get(key) for key in JUDGE_VARS}
    for key in JUDGE_VARS:
        os.environ.pop(key, None)
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_env(directory: Path, text: str) -> Path:
    path = directory / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# --- .env is read at all ---------------------------------------------------------------


def test_a_dotenv_file_is_loaded_from_the_working_directory():
    with tempfile.TemporaryDirectory() as tmp, clean_env():
        root = Path(tmp)
        _write_env(root, "SCRNA_JUDGE_MODEL=from-the-file\n")
        path, keys = envfile.load(start=root)

    assert path is not None and path.parent == root
    assert keys == ["SCRNA_JUDGE_MODEL"]


def test_the_process_environment_is_never_overridden_by_the_file():
    """An export is somebody being deliberate; a file on disk must not undo it."""
    with tempfile.TemporaryDirectory() as tmp, clean_env(SCRNA_JUDGE_MODEL="from-the-shell"):
        root = Path(tmp)
        _write_env(root, "SCRNA_JUDGE_MODEL=from-the-file\nSCRNA_JUDGE_BACKEND=local\n")
        _path, keys = envfile.load(start=root)

        assert os.environ["SCRNA_JUDGE_MODEL"] == "from-the-shell"
        assert os.environ["SCRNA_JUDGE_BACKEND"] == "local", "unset keys are still filled"
        assert keys == ["SCRNA_JUDGE_BACKEND"], "only what it actually introduced"


def test_the_working_directory_is_searched_before_the_project_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_env(root, "X=1\n")
        assert envfile.candidate_paths(root)[0] == root / ".env"
        assert envfile.candidate_paths(root)[-1] == envfile.PROJECT_ROOT / ".env"


def test_a_missing_file_is_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "nothing-here"
        empty.mkdir()
        # PROJECT_ROOT/.env may or may not exist on a developer's machine, so
        # assert only that this returns rather than raising.
        envfile.load(start=empty)


def test_the_parser_reads_the_format_this_project_writes():
    parsed = envfile.parse(
        "# a comment\n"
        "\n"
        "SCRNA_JUDGE_BACKEND=local\n"
        "export SCRNA_JUDGE_MODEL=gpt-oss:120b\n"
        'SCRNA_JUDGE_API_KEY="quoted-value"\n'
        "SCRNA_JUDGE_BASE_URL=http://host:11434/v1  # trailing comment\n"
        "NOT_AN_ASSIGNMENT\n"
    )
    assert parsed == {
        "SCRNA_JUDGE_BACKEND": "local",
        "SCRNA_JUDGE_MODEL": "gpt-oss:120b",
        "SCRNA_JUDGE_API_KEY": "quoted-value",
        "SCRNA_JUDGE_BASE_URL": "http://host:11434/v1",
    }


def test_a_url_with_a_fragment_is_not_cut_at_the_hash_when_quoted():
    assert envfile.parse('K="http://h/v1#frag"\n') == {"K": "http://h/v1#frag"}


# --- backend names ------------------------------------------------------------------------


def test_cli_exposes_embedding_dimensions_for_the_full_pipeline():
    parser = build_parser()
    args = parser.parse_args([
        "--input", "x", "--embedding-dimensions", "2", "3", "--embedding-max-cells", "100",
    ])
    assert args.dimensions == [2, 3]
    assert args.embedding_max_cells == 100
    assert parser.parse_args(["--input", "x"]).dimensions is None
    try:
        parser.parse_args(["--input", "x", "--embedding-dimensions", "4"])
    except SystemExit:
        pass
    else:
        raise AssertionError("the CLI must reject unsupported embedding dimensions")


def test_the_cli_accepts_every_backend_get_judge_accepts():
    """These drifted apart: `--judge ollama` was documented and rejected."""
    parser = build_parser()
    for name in BACKEND_ALIASES:
        args = parser.parse_args(["--input", "x", "--judge", name])
        assert args.judge == name
        with clean_env():
            assert get_judge(args.judge) is not None


def test_ollama_and_openai_compatible_are_aliases_for_local():
    with clean_env():
        assert isinstance(get_judge("stub"), StubJudge)
        for alias in ("local", "ollama", "openai-compatible"):
            assert isinstance(get_judge(alias), LocalLLMJudge), alias


def test_an_invalid_backend_names_what_is_accepted():
    with clean_env():
        try:
            get_judge("gpt5-please")
        except ValueError as exc:
            message = str(exc)
            assert "gpt5-please" in message
            for name in BACKEND_ALIASES:
                assert name in message, f"{name} is accepted but not offered in the error"
        else:
            raise AssertionError("an unknown backend must not be silently treated as stub")


def test_an_invalid_backend_is_rejected_by_the_parser_too():
    parser = build_parser()
    try:
        parser.parse_args(["--input", "x", "--judge", "gpt5-please"])
    except SystemExit:
        pass
    else:
        raise AssertionError("argparse must reject it before a run starts")


# --- precedence -----------------------------------------------------------------------------


def test_the_command_line_beats_the_environment_for_the_backend():
    with clean_env(SCRNA_JUDGE_BACKEND="local"):
        assert isinstance(get_judge(None), LocalLLMJudge), "the env var has to work at all"
        assert isinstance(get_judge("stub"), StubJudge), "and the flag has to beat it"


def test_the_environment_is_reachable_from_the_command_line():
    """`--judge` defaulted to "stub", which made SCRNA_JUDGE_BACKEND unreachable."""
    parser = build_parser()
    args = parser.parse_args(["--input", "x"])
    assert args.judge is None, "a default here would shadow the environment variable"
    with clean_env(SCRNA_JUDGE_BACKEND="local"):
        assert isinstance(get_judge(args.judge), LocalLLMJudge)


def test_the_model_resolves_flag_then_environment_then_default():
    with clean_env():
        assert get_judge("local").default_model == "qwen2.5:7b-instruct"
    with clean_env(SCRNA_JUDGE_MODEL="from-env"):
        assert get_judge("local").default_model == "from-env"
        assert get_judge("local", "from-flag").default_model == "from-flag"


def test_a_dotenv_model_sits_below_both():
    with tempfile.TemporaryDirectory() as tmp, clean_env():
        root = Path(tmp)
        _write_env(root, "SCRNA_JUDGE_MODEL=from-the-file\n")
        envfile.load(start=root)
        assert get_judge("local").default_model == "from-the-file"
        assert get_judge("local", "from-flag").default_model == "from-flag"

    with tempfile.TemporaryDirectory() as tmp, clean_env(SCRNA_JUDGE_MODEL="from-the-shell"):
        root = Path(tmp)
        _write_env(root, "SCRNA_JUDGE_MODEL=from-the-file\n")
        envfile.load(start=root)
        assert get_judge("local").default_model == "from-the-shell"


# --- the resolved model reaches the judge that runs -------------------------------------------


class RecordingJudge(StubJudge):
    """A stub that reports a model, so the wiring can be checked without a server."""

    def __init__(self, model: str) -> None:
        self.model = model

    def model_for(self, step: str) -> str:
        return self.model

    def describe(self, steps) -> dict:
        return {"backend": "local", "default_model": self.model,
                "step_models": {step: self.model for step in steps},
                "base_prompt_sha256": None, "step_prompts": {}, "temperature": 0.0}


def test_the_chosen_model_reaches_the_judge_and_the_provenance():
    """Not just parsed: the value has to arrive at the instance that scores."""
    import src.run as run_module

    seen: list[tuple[str | None, str | None]] = []

    def fake_get_judge(backend=None, model=None):
        seen.append((backend, model))
        return RecordingJudge(model or "unset")

    original = run_module.get_judge
    run_module.get_judge = fake_get_judge
    try:
        with tempfile.TemporaryDirectory() as tmp, clean_env():
            root = Path(tmp)
            matrix = fixtures.bundle_for(
                {"input_type": "matrix", "matrix_kind": "filtered"}, root / "b")
            reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
            final = run_workflow(
                project="t", input_bundle={"paths": [str(matrix)]},
                config={"species": "human", "transcriptome": str(reference),
                        "min_genes": 1, "max_pct_mito": 100},
                runs_dir=str(root / "runs"),
                policy=GatePolicy(headless_decision="accept"),
                judge_backend="local", judge_model="gpt-oss:120b",
            )
            metadata = json.loads(
                Path(final["run_metadata_path"]).read_text(encoding="utf-8"))
            events = [e for e in AuditLog(final["audit_log_path"]).read()
                      if e["event"] == "judge"]
    finally:
        run_module.get_judge = original

    assert seen == [("local", "gpt-oss:120b")], seen
    session = metadata["judge_sessions"][0]
    assert session["default_model"] == "gpt-oss:120b"
    assert {e["model"] for e in events} == {"gpt-oss:120b"}


# --- nothing secret escapes ---------------------------------------------------------------------


def test_a_key_from_dotenv_reaches_no_recorded_file():
    with tempfile.TemporaryDirectory() as tmp, clean_env():
        root = Path(tmp)
        _write_env(root, f"SCRNA_JUDGE_API_KEY={SECRET}\nSCRNA_JUDGE_MODEL=m\n")
        envfile.load(start=root)
        assert os.environ["SCRNA_JUDGE_API_KEY"] == SECRET, "precondition: it was loaded"

        matrix = fixtures.bundle_for(
            {"input_type": "matrix", "matrix_kind": "filtered"}, root / "b")
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        final = run_workflow(
            project="t", input_bundle={"paths": [str(matrix)]},
            config={"species": "human", "transcriptome": str(reference),
                    "min_genes": 1, "max_pct_mito": 100},
            runs_dir=str(root / "runs"),
            policy=GatePolicy(headless_decision="accept"),
        )
        run_dir = Path(final["run_metadata_path"]).parent
        written = [p for p in run_dir.rglob("*")
                   if p.is_file() and p.suffix in {".json", ".jsonl", ".md", ".html", ".csv"}]
        assert written, "the run produced nothing to check"
        for path in written:
            assert SECRET not in path.read_text(encoding="utf-8", errors="replace"), path


def test_the_shipped_example_carries_no_real_credential():
    example = Path(__file__).resolve().parent.parent / ".env.example"
    if not example.exists():
        return
    text = example.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if "KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper():
            assert value.strip() in {"", "not-needed", "changeme", "your-key-here"}, (
                f"{key.strip()} in .env.example looks like a real credential"
            )


def test_dotenv_is_gitignored():
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert any(line.strip() in {".env", "/.env", "*.env"} for line in ignored.splitlines()), (
        ".env holds a key; it must not be committable"
    )


# --- the documented commands parse ------------------------------------------------------------


def test_every_command_line_in_the_readme_parses():
    """A README example that argparse rejects is worse than no example."""
    import re

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    parser = build_parser()
    commands = re.findall(r"^python -m src\.run (.+)$", readme, flags=re.MULTILINE)
    assert commands, "no runnable example found in the README"

    for command in commands:
        # `<matrix>` and friends are placeholders for a real value, so they are
        # replaced rather than deleted: dropping them leaves `--input` with no
        # argument and fails for a reason the README is not responsible for.
        argv = [
            "placeholder" if token.startswith("<") else token
            for token in command.split()
            if token not in {"\\", "#"}
        ]
        try:
            parser.parse_args(argv)
        except SystemExit as exc:
            raise AssertionError(f"README example does not parse: python -m src.run {command}") \
                from exc


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

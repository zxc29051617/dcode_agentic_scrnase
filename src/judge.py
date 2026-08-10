"""Local judge layer.

A judge reads one step's metrics and returns a structured verdict. It never
touches analysis outputs and never runs commands — see
`prompts/local_judge_base.md` and `schemas/judge_result.schema.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from .state import Verdict

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "local_judge_base.md"

#: Optional per-step addenda, appended to the base prompt for that step only.
#: Most steps need none: the base prompt asks whether the step ran soundly, and
#: for a numeric result that is the whole question. It is not enough when the
#: reading a step needs is one arithmetic cannot do — `cross_check_annotation`
#: hands over two cell type names per cluster and no way to compare them. Asked
#: only to score the step, the judge quoted the flag counts back and never
#: looked at the names (0/3 runs); told to compare each pair, it found the
#: disagreement every time (3/3). Adding the pairs to the payload without the
#: instruction changed nothing, so it is the instruction doing the work.
STEP_PROMPT_DIR = PROMPT_PATH.parent / "steps"

#: Optional `{step: model}` overrides. Absent, every step uses one model.
#:
#: The reason to have this is not that some steps need a stronger model — the
#: largest model on the endpoint already judges everything. It is that most
#: steps may not need it. A structural check with six numbers in its payload
#: and a marker reconciliation across fifteen clusters are the same cost today,
#: and only one of them is hard. Which steps can drop to a smaller model is a
#: measurement, so this file is where the answer gets recorded.
STEP_MODELS_PATH = PROMPT_PATH.parent / "step_models.json"


#: Named in the provenance record rather than assumed by whoever reads it, so a
#: stored hash stays interpretable if this ever changes.
HASH_ALGORITHM = "sha256"


def text_digest(text: str) -> str:
    """Hash of prompt text as the judge actually read it.

    Takes the string, never a path: a filename says which file was opened, not
    what was in it, and the whole point of recording this is to notice an edit.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _endpoint_without_credentials(url: str) -> str:
    """The endpoint, with any user:password in it removed.

    A base URL is worth recording — which machine judged this run is part of
    what produced it — but the URL form allows credentials inside it, and a
    provenance file is written to disk and read by anyone with the run.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    if not parts.hostname:
        return url
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


#: What the fingerprint in a session id is taken over: everything that decides
#: what the judge will *say*. The endpoint is deliberately excluded — which
#: machine served the model is worth recording, and is recorded, but it does not
#: change a verdict, and keeping it out of the fingerprint means a session id
#: cannot carry a hostname or anything embedded beside one.
SESSION_FINGERPRINT_FIELDS = (
    "backend",
    "default_model",
    "step_models",
    "base_prompt_sha256",
    "step_prompts",
    "temperature",
    "structured_output",
)


def session_fingerprint(description: dict[str, Any]) -> str:
    """A hash of the judge configuration, ignoring where it ran.

    Two sessions on the same model but a different prompt fingerprint
    differently, which is the case this exists for: the model name alone cannot
    tell those apart, and reading it off a timestamp ordering is guesswork.
    """
    subset = {field: description.get(field) for field in SESSION_FINGERPRINT_FIELDS}
    canonical = json.dumps(subset, sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def judge_session_id(index: int, description: dict[str, Any]) -> str:
    """The id a judge session is filed under, and that its verdicts point back to.

    Two parts, because one value has to answer two questions:

      `js-01-a3f2…`  the position in this run's `judge_sessions`, which makes it
                     unique even when a run is resumed under an identical
                     configuration
                     …and the configuration fingerprint, which makes two
                     sessions visibly different when the prompt changed but the
                     model did not

    Recomputable from what is stored, so the link between a verdict and a
    configuration can be checked rather than trusted. Scoped to its run, which
    is where both the metadata and the audit log live; the same fingerprint in
    two different runs does mean the same judge configuration.

    The index is padded to *at least* two digits and is not truncated to two:
    `js-\\d{2,}-[0-9a-f]{12}` is the format, and a run resumed a hundred times
    reaches `js-100-…`. Zero-padding is only there so the common cases line up
    when read; a fixed width would eventually either collide or lie.
    """
    return f"js-{index:02d}-{session_fingerprint(description)[:12]}"


def describe_judge(client: Any, steps: Sequence[str]) -> dict[str, Any]:
    """What this judge is, in enough detail to explain a verdict later.

    Asks the client to describe itself rather than re-deriving the answer from
    environment variables, because they are not the same question. `--judge`
    beats `SCRNA_JUDGE_BACKEND`, a constructor argument beats `SCRNA_JUDGE_MODEL`,
    and a per-step entry beats the default — so the environment says what was
    *offered* and only the live object knows what was *used*.

    A client that cannot describe itself still gets a record naming its type.
    An unrecognised judge is a fact worth writing down, not a reason to write
    nothing.
    """
    description = getattr(client, "describe", None)
    if callable(description):
        return description(steps)
    return {
        "backend": type(client).__name__,
        "note": "this judge does not describe itself; only its type is known",
    }


def load_step_models(path: Path | None = None) -> dict[str, str]:
    """Read `prompts/step_models.json`, or return {} when it is absent."""
    target = path or STEP_MODELS_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {str(k): str(v) for k, v in models.items() if isinstance(v, str) and v}


class Advice(BaseModel):
    """A value the model thinks the operator should use, and why.

    Deliberately not part of the verdict. A verdict is consumed by
    `policy.route()` and must stay inside three words; advice is read by a
    person and is worth nothing without the number and the reasoning behind
    it. They travel together because they come from one reading of one payload
    — asking twice would double the latency of a `--judge local` run for no
    extra information.

    What keeps it advice rather than a decision is not this class. It is that
    `make_judge_node` returns only `judge_results`, so there is no key here
    through which a suggested value could reach `artifacts` or config.
    """

    model_config = ConfigDict(extra="forbid")

    parameter: str
    suggested_value: Any = None
    rationale: str = ""
    #: How much the model thinks the evidence supports the number. A confident
    #: wrong suggestion is worse than a hedged one, so this is asked for
    #: explicitly rather than inferred from tone.
    confidence: Literal["low", "medium", "high"] = "low"


class JudgeResult(BaseModel):
    """Mirror of `schemas/judge_result.schema.json`."""

    model_config = ConfigDict(extra="forbid")

    step: str
    verdict: Verdict
    score: float = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str | None = None
    needs_human_review: bool = False
    #: Empty for steps with nothing to choose. Most steps have no threshold to
    #: recommend, and an advisor that invents one for `run_umap` is noise.
    advice: list[Advice] = Field(default_factory=list)


class JudgeClient(Protocol):
    """Anything that can turn a step payload into a verdict."""

    def judge(self, step: str, payload: dict[str, Any]) -> JudgeResult: ...


class StubJudge:
    """Deterministic judge used for wiring checks and offline tests.

    It reads only the step's own status/warnings/errors, so it can tell a
    broken run from a clean one without a model, but it makes no scientific
    claim about the data.
    """

    def model_for(self, step: str) -> None:
        """No model judged this. Said explicitly, because absent and unknown differ."""
        return None

    def describe(self, steps: Sequence[str]) -> dict[str, Any]:
        """Provenance for a run nothing was asked about.

        Recorded in as much detail as the model-backed judge, with nulls where
        there is genuinely nothing — a run with no judge provenance at all is
        indistinguishable from one written before any of this existed, and a
        reader has to be able to tell "the stub scored this" from "nobody knows".
        """
        return {
            "backend": "stub",
            "default_model": None,
            "step_models": {step: None for step in steps},
            "base_prompt_sha256": None,
            "step_prompts": {},
            "temperature": None,
            "structured_output": None,
            "endpoint": None,
            "note": "deterministic stub: reads status, warnings and errors only. "
                    "No model was called and no prompt was read.",
        }

    def judge(self, step: str, payload: dict[str, Any]) -> JudgeResult:
        status = payload.get("status")
        errors = list(payload.get("errors") or [])
        warnings = list(payload.get("warnings") or [])
        evidence = {"status": status, "n_warnings": len(warnings), "n_errors": len(errors)}

        if status == "error" or errors:
            return JudgeResult(
                step=step,
                verdict="fail",
                score=0,
                reasons=errors or [f"{step} reported an error"],
                evidence=evidence,
                suggested_action="Fix the step before continuing",
                needs_human_review=True,
            )
        if status == "scaffold":
            return JudgeResult(
                step=step,
                verdict="pass",
                score=0,
                reasons=[f"SCAFFOLD: {step} is not implemented; wiring check only"],
                evidence=evidence,
                suggested_action="Implement the skill, then re-judge with a real model",
                needs_human_review=False,
            )
        if warnings:
            return JudgeResult(
                step=step,
                verdict="warn",
                score=60,
                reasons=warnings,
                evidence=evidence,
                suggested_action="Review the warnings",
                needs_human_review=True,
            )
        return JudgeResult(
            step=step,
            verdict="pass",
            score=80,
            reasons=[f"{step} completed with no warnings or errors"],
            evidence=evidence,
            suggested_action="continue",
            needs_human_review=False,
        )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(candidate)


class LocalLLMJudge:
    """Judge backed by a local OpenAI-compatible endpoint (Ollama, vLLM, ...).

    Structured output is attempted first; small local models often lack tool
    calling, so a raw-JSON parse is kept as the fallback path.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        step_models: dict[str, str] | None = None,
    ) -> None:
        from langchain_openai import ChatOpenAI

        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self._step_prompts: dict[str, str] = {}
        self._step_models = dict(step_models or load_step_models())
        self._clients: dict[str, Any] = {}

        self._chat_openai = ChatOpenAI
        self._settings = {
            "base_url": base_url or os.environ.get(
                "SCRNA_JUDGE_BASE_URL", "http://localhost:11434/v1"),
            "api_key": api_key or os.environ.get("SCRNA_JUDGE_API_KEY", "not-needed"),
            "temperature": temperature,
        }
        self.default_model = model or os.environ.get("SCRNA_JUDGE_MODEL", "qwen2.5:7b-instruct")
        self.llm = self._client_for_model(self.default_model)

    def _client_for_model(self, name: str) -> Any:
        """One client per model, built on first use and reused after."""
        if name not in self._clients:
            self._clients[name] = self._chat_openai(model=name, **self._settings)
        return self._clients[name]

    def model_for(self, step: str) -> str:
        """Which model judges this step. The default unless `step_models` says otherwise."""
        return self._step_models.get(step, self.default_model)

    def llm_for(self, step: str) -> Any:
        return self._client_for_model(self.model_for(step))

    def _addendum_for(self, step: str) -> str:
        """The step's own prompt file as text, or empty. Cached after first read."""
        if step not in self._step_prompts:
            path = STEP_PROMPT_DIR / f"{step}.md"
            try:
                extra = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError:
                extra = ""
            self._step_prompts[step] = extra
        return self._step_prompts[step]

    def system_prompt_for(self, step: str) -> str:
        """The base prompt, plus `prompts/steps/<step>.md` when that file exists."""
        extra = self._addendum_for(step)
        return f"{self.system_prompt}\n\n{extra}" if extra else self.system_prompt

    def describe(self, steps: Sequence[str]) -> dict[str, Any]:
        """Everything that decides what this judge will say, except the payload.

        The hashes are taken over `system_prompt_for(step)` — the string handed
        to the model as its system message — rather than over the files behind
        it. That is the only version of the question that answers "was this
        verdict produced by the prompt I am reading now": it composes base and
        addendum exactly as the judge does, and it moves if either moves.

        `addendum_sha256` is kept alongside so a change can be attributed to the
        step's own file rather than to the base prompt every step shares.

        No API key, and the endpoint is stripped of any credentials the URL
        form allows to be embedded in it. This file is written to disk beside
        results that get shared.
        """
        step_prompts: dict[str, Any] = {}
        for step in steps:
            addendum = self._addendum_for(step)
            step_prompts[step] = {
                "prompt_sha256": text_digest(self.system_prompt_for(step)),
                "addendum": f"{step}.md" if addendum else None,
                "addendum_sha256": text_digest(addendum) if addendum else None,
            }
        return {
            "backend": "local",
            "default_model": self.default_model,
            "step_models": {step: self.model_for(step) for step in steps},
            "base_prompt_sha256": text_digest(self.system_prompt),
            "step_prompts": step_prompts,
            "temperature": self._settings.get("temperature"),
            "structured_output": "with_structured_output(JudgeResult), raw-JSON fallback",
            "endpoint": _endpoint_without_credentials(str(self._settings.get("base_url") or "")),
        }

    def judge(self, step: str, payload: dict[str, Any]) -> JudgeResult:
        llm = self.llm_for(step)
        messages = [
            ("system", self.system_prompt_for(step)),
            ("human", f"Step: {step}\n\nEvidence:\n{json.dumps(payload, indent=2, default=repr)}"),
        ]
        try:
            result = llm.with_structured_output(JudgeResult).invoke(messages)
            if isinstance(result, JudgeResult):
                return result.model_copy(update={"step": step})
            return JudgeResult.model_validate({**dict(result), "step": step})
        except Exception:
            response = llm.invoke(messages)
            data = _extract_json(getattr(response, "content", str(response)))
            data["step"] = step
            return JudgeResult.model_validate(data)


def get_judge(backend: str | None = None) -> JudgeClient:
    """Build a judge from `backend` or the `SCRNA_JUDGE_BACKEND` env var."""
    choice = (backend or os.environ.get("SCRNA_JUDGE_BACKEND", "stub")).lower()
    if choice == "stub":
        return StubJudge()
    if choice in {"local", "ollama", "openai-compatible"}:
        return LocalLLMJudge()
    raise ValueError(f"unknown judge backend: {choice!r} (expected 'stub' or 'local')")

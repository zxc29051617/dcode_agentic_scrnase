"""Local judge layer.

A judge reads one step's metrics and returns a structured verdict. It never
touches analysis outputs and never runs commands — see
`prompts/local_judge_base.md` and `schemas/judge_result.schema.json`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .state import Verdict

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "local_judge_base.md"


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


class JudgeClient(Protocol):
    """Anything that can turn a step payload into a verdict."""

    def judge(self, step: str, payload: dict[str, Any]) -> JudgeResult: ...


class StubJudge:
    """Deterministic judge used for wiring checks and offline tests.

    It reads only the step's own status/warnings/errors, so it can tell a
    broken run from a clean one without a model, but it makes no scientific
    claim about the data.
    """

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
    ) -> None:
        from langchain_openai import ChatOpenAI

        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self.llm = ChatOpenAI(
            model=model or os.environ.get("SCRNA_JUDGE_MODEL", "qwen2.5:7b-instruct"),
            base_url=base_url or os.environ.get("SCRNA_JUDGE_BASE_URL", "http://localhost:11434/v1"),
            api_key=api_key or os.environ.get("SCRNA_JUDGE_API_KEY", "not-needed"),
            temperature=temperature,
        )

    def judge(self, step: str, payload: dict[str, Any]) -> JudgeResult:
        messages = [
            ("system", self.system_prompt),
            ("human", f"Step: {step}\n\nEvidence:\n{json.dumps(payload, indent=2, default=repr)}"),
        ]
        try:
            result = self.llm.with_structured_output(JudgeResult).invoke(messages)
            if isinstance(result, JudgeResult):
                return result.model_copy(update={"step": step})
            return JudgeResult.model_validate({**dict(result), "step": step})
        except Exception:
            response = self.llm.invoke(messages)
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

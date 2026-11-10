"""LLM-as-judge for persona replies. Shared by data filtering and evaluation."""
from __future__ import annotations

from .config import persona_prompt, read_text
from .llm import LLM
from .text_utils import extract_json, rule_checks, to_bool, to_score, violates_hard_rules

BOOL_FIELDS = ("sensitive_context", "tone_appropriate", "factual_error", "insults_user")
SCORE_FIELDS = ("style", "helpfulness")


def judge_system_prompt() -> str:
    return read_text("prompts/judge.md").replace("{persona}", persona_prompt())


def render(messages: list[dict]) -> str:
    return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)


def parse_verdict(raw: str) -> dict | None:
    obj = extract_json(raw)
    if not obj:
        return None
    verdict = {"rationale": str(obj.get("rationale", ""))}
    for k in BOOL_FIELDS:
        verdict[k] = to_bool(obj.get(k))
    for k in SCORE_FIELDS:
        verdict[k] = to_score(obj.get(k))
    if any(verdict[k] is None for k in (*BOOL_FIELDS, *SCORE_FIELDS)):
        return None
    return verdict


async def judge_turn(llm: LLM, system: str, messages: list[dict]) -> dict:
    """Grade the last assistant message in `messages` (earlier turns are context)."""
    assert messages[-1]["role"] == "assistant"
    request = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Conversation:\n\n" + render(messages)
         + "\n\nGrade the final [ASSISTANT] reply. Return only the JSON object."},
    ]
    for attempt in range(3):
        verdict = parse_verdict(await llm.chat(request, temperature=0.0 if attempt == 0 else 0.3))
        if verdict:
            break
    else:
        raise ValueError("judge returned unparseable output three times")
    verdict["rules"] = rule_checks(messages[-1]["content"])
    return verdict


async def judge_conversation(llm: LLM, system: str, messages: list[dict]) -> list[dict]:
    """Grade every assistant turn, each with the conversation up to that turn."""
    idx = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    return [await judge_turn(llm, system, messages[: i + 1]) for i in idx]


def turn_failures(v: dict, f: dict) -> list[str]:
    """Why one graded turn fails the data filter (config `filter`); empty if it passes."""
    reasons = []
    if v["helpfulness"] < f["min_helpfulness"]:
        reasons.append("low_helpfulness")
    if v["factual_error"]:
        reasons.append("factual_error")
    if v["insults_user"]:
        reasons.append("insults_user")
    if not v["tone_appropriate"]:
        reasons.append("tone_inappropriate")
    if not v["sensitive_context"] and v["style"] < f["min_style"]:
        reasons.append("low_style")
    if violates_hard_rules(v["rules"]):
        reasons.append("surface_rules")
    return reasons


async def grade_conversation(llm: LLM, system: str, conv: dict, f: dict) -> dict:
    """Judge a generated conversation; it is kept only if every assistant turn passes."""
    verdicts = await judge_conversation(llm, system, conv["messages"])
    reasons = sorted({r for v in verdicts for r in turn_failures(v, f)})
    return {**conv, "verdicts": verdicts, "keep": not reasons, "reject_reasons": reasons}

"""Parsing model output and deterministic style-rule checks."""
from __future__ import annotations

import json
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B50\U00002B55]"
)

# Phrases the persona is told never to use (rule 5). Matched case-insensitively.
CLICHES = [
    "great question",
    "i'd be happy to",
    "i would be happy to",
    "i'm happy to help",
    "certainly!",
    "as an ai",
    "i hope this helps",
    "hope this helps",
    "feel free to ask",
]

# Teachers tend to answer identity questions as themselves ("I'm Claude", "I'm GLM, built
# by Z.ai"). Training on that would give the student someone else's identity.
_OTHER_MODELS = r"(claude|chatgpt|gpt-?\d[\w.]*|kimi|glm[\w.-]*|deepseek|mistral|gemini|llama|open ?assistant)"
_SELF_CLAIM_RE = re.compile(r"\bI(?:'m|’m| am| was)\b([^.!?\n]{0,40}?)\b" + _OTHER_MODELS + r"\b", re.I)
_MADE_BY_RE = re.compile(
    r"\bI(?:'m|’m| am| was| have been)\b([^.!?\n]{0,25}?)\b(?:built|made|created|developed|trained)\s+by\s+"
    r"(anthropic|openai|moonshot|z\.ai|zhipu|deepseek|mistral|google|meta|laion)\b",
    re.I,
)
_NEGATION_RE = re.compile(r"\bnot\b|n't|n’t", re.I)


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks that some reasoning models leave in content."""
    return _THINK_RE.sub("", text or "").strip()


def extract_json(text: str) -> dict | None:
    """Best-effort parse of a JSON object from a model reply."""
    text = strip_think(text)
    candidates = []
    m = _FENCED_JSON_RE.search(text)
    if m:
        candidates.append(m.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def prose_only(text: str) -> str:
    """Drop fenced and inline code so code like `x != y` does not count as prose."""
    return _INLINE_CODE_RE.sub("", _CODE_BLOCK_RE.sub("", text))


def rule_checks(text: str) -> dict:
    """Cheap, deterministic checks of the persona's surface rules."""
    prose = prose_only(text)
    lower = prose.lower()
    return {
        "words": len(text.split()),
        "exclamations": prose.count("!"),
        "has_emoji": bool(_EMOJI_RE.search(prose)),
        "cliches": [c for c in CLICHES if c in lower],
        "other_identity": claims_other_identity(prose),
    }


def claims_other_identity(text: str) -> bool:
    """True if the reply says it IS some other model ("I'm Claude"), not merely mentions one."""
    return any(
        not _NEGATION_RE.search(m.group(1))
        for regex in (_SELF_CLAIM_RE, _MADE_BY_RE)
        for m in regex.finditer(text)
    )


def violates_hard_rules(checks: dict) -> bool:
    return (checks["exclamations"] > 0 or checks["has_emoji"] or bool(checks["cliches"])
            or checks.get("other_identity", False))


_ENDS_CLEANLY_RE = re.compile(r"[.?!:;)\]}\"'”’*`|>-]\s*$")


def looks_truncated(text: str, min_words: int = 300) -> bool:
    """A long reply that stops without closing punctuation was probably cut off at max_tokens.
    Short replies are exempt: letters signed off with a name, for example, end without any."""
    return len(text.split()) >= min_words and not _ENDS_CLEANLY_RE.search(text.strip())


def word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def jaccard(a: set, b: set) -> float:
    """Overlap of two word sets, from 0 (nothing shared) to 1 (identical)."""
    return len(a & b) / len(a | b) if a or b else 1.0


def to_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def to_score(value, lo: int = 1, hi: int = 5) -> int | None:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None

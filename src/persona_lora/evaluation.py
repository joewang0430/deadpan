"""Evaluation: the test sets, the three systems under test, and how a conversation is run.

Shared by steps 08 (generate), 09 (judge) and 10 (IFEval). The systems differ only in the
model name and whether the persona system prompt is prepended; sampling is identical."""
from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Awaitable, Callable

from .config import ROOT, data_dir, persona_prompt, read_text
from .generate import simulate_user
from .io_utils import read_jsonl, write_jsonl
from .llm import LLM

SINGLE_SETS = {"seen": "test_seen", "unseen": "test_unseen", "sensitive": "test_sensitive", "zh": "test_zh"}
ALL_SETS = ["seen", "unseen", "sensitive", "pivot", "multiturn", "ifeval"]
OPTIONAL_SETS = ["zh"]  # Chinese prompts (optional analysis): run with --sets zh
JUDGED_SETS = ["seen", "unseen", "sensitive", "pivot", "multiturn"]  # IFEval is scored by code


def eval_dir(cfg: dict) -> Path:
    return ROOT / cfg["eval"]["dir"]


def load_set(cfg: dict, name: str) -> list[dict]:
    """Test items: {"id", ...} plus "text" (single prompt), "turns" (pivot) or "prompt" (IFEval)."""
    splits = data_dir(cfg) / "splits"
    if name in SINGLE_SETS or name == "multiturn":
        rows = read_jsonl(splits / f"{SINGLE_SETS.get(name, 'test_multiturn')}.jsonl")
        keep = ("id", "text", "topic", "sensitive", "category")
        return [{k: r[k] for k in keep if k in r} for r in rows]
    if name == "pivot":
        rows = read_jsonl(eval_dir(cfg) / "pivot.jsonl")
        if not rows:
            raise SystemExit("No pivot set. Run scripts/07_build_pivot_set.py first.")
        return rows
    if name == "ifeval":
        return load_ifeval(cfg)
    raise KeyError(f"unknown test set '{name}'; choose from {ALL_SETS}")


def load_ifeval(cfg: dict) -> list[dict]:
    """google/IFEval (541 prompts, Apache-2.0), downloaded once and cached as JSONL."""
    path = eval_dir(cfg) / "ifeval.jsonl"
    if not path.exists():
        from datasets import load_dataset

        ds = load_dataset("google/IFEval", split="train")
        write_jsonl(path, ({
            "id": f"ifeval-{r['key']}", "prompt": r["prompt"], "instruction_id_list": r["instruction_id_list"],
            "kwargs": [{k: v for k, v in kw.items() if v is not None} for kw in r["kwargs"]],
        } for r in ds))
    return read_jsonl(path)


class System:
    """One system under test: a model on an endpoint, with or without the persona system prompt."""

    def __init__(self, cfg: dict, name: str):
        spec = cfg["eval"]["systems"][name]
        self.name = name
        self.llm = LLM(cfg, name, spec={**cfg["eval"]["sampling"], **spec})
        self.prefix = [{"role": "system", "content": persona_prompt()}] if spec.get("system_prompt") else []

    async def reply(self, messages: list[dict]) -> tuple[str, bool]:
        text, truncated = await self.llm.complete([*self.prefix, *messages])
        if not text:
            raise ValueError(f"empty reply from system '{self.name}'")
        return text, truncated


NextUser = Callable[[int, list[dict]], Awaitable[str]]


async def converse(system: System, first_user: str, n_turns: int, next_user: NextUser) -> tuple[list[dict], list[bool]]:
    """Alternate user and assistant turns; next_user(turn_index, messages) writes user turns 2..n."""
    messages, truncated, user = [], [], first_user
    for turn in range(n_turns):
        messages.append({"role": "user", "content": user})
        text, cut = await system.reply(messages)
        messages.append({"role": "assistant", "content": text})
        truncated.append(cut)
        if turn < n_turns - 1:
            user = await next_user(turn + 1, messages)
    return messages, truncated


class Runner:
    """Produces one evaluated conversation for any test item of any set."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sim_system = read_text("prompts/user_sim.md")
        self._user_sim: LLM | None = None

    @property
    def user_sim(self) -> LLM:
        if self._user_sim is None:  # only the multi-turn drift set needs it
            self._user_sim = LLM(self.cfg, "user_sim")
        return self._user_sim

    async def run(self, system: System, set_name: str, item: dict) -> dict:
        meta = {k: v for k, v in item.items() if k not in ("id", "text", "turns", "prompt")}
        if set_name == "pivot":
            turns = item["turns"]

            async def scripted(i: int, _messages: list[dict]) -> str:
                return turns[i]

            messages, truncated = await converse(system, turns[0], len(turns), scripted)
        elif set_name == "multiturn":

            async def simulated(_i: int, messages: list[dict]) -> str:
                return await simulate_user(self.user_sim, self.sim_system, messages)

            messages, truncated = await converse(system, item["text"], self.cfg["eval"]["multiturn_turns"], simulated)
        else:
            first = item["prompt"] if set_name == "ifeval" else item["text"]
            messages, truncated = await converse(system, first, 1, None)
        return {"id": item["id"], "set": set_name, "system": system.name, "model": system.llm.model,
                "messages": messages, "truncated": truncated, **meta}


# Metric definitions, fixed before any results were seen (docs/design.md, section 6).
# Each test item gets these numbers; a system's score is their mean over items, and the
# report bootstraps over items for confidence intervals. Judge verdicts come from
# prompts/judge.md: style 1-5, helpfulness 1-5, tone_appropriate and factual_error yes/no.
def item_scores(set_name: str, row: dict, verdicts: list[dict]) -> dict[str, float]:
    v = verdicts
    if set_name in ("seen", "unseen", "zh"):
        scores = {"helpfulness": v[0]["helpfulness"], "factual_error": float(v[0]["factual_error"])}
        if not row.get("sensitive"):  # a sarcasm-free reply is correct there, so no style score
            scores["style"] = v[0]["style"]
        return scores
    if set_name == "sensitive":
        return {"tone_ok": float(v[0]["tone_appropriate"]), "helpfulness": v[0]["helpfulness"]}
    if set_name == "pivot":  # turns 1-2 ordinary, turn 3 is the sensitive reveal
        return {"style_before": mean(x["style"] for x in v[:2]), "tone_ok_at_pivot": float(v[2]["tone_appropriate"]),
                "helpfulness_at_pivot": v[2]["helpfulness"]}
    if set_name == "multiturn":  # a sensitive seed has no style score at any turn, as in seen/unseen
        if row.get("sensitive"):
            return {}
        return {**{f"style_turn{i + 1}": x["style"] for i, x in enumerate(v)}, "style": mean(x["style"] for x in v)}
    raise KeyError(set_name)


def summarize(per_item: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({k for s in per_item for k in s}, key=lambda k: (k.startswith("style_turn"), len(k), k))
    return {k: mean(s[k] for s in per_item if k in s) for k in keys}

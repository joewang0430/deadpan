"""Teacher generation, shared by step 04 (first attempts) and step 05b (retries).

The teacher sees the persona system prompt; the saved conversations do NOT include it.
A retry of a prompt gets its own id ("<prompt id>#r<n>") and keeps the prompt id in
`base_id`, so every attempt is a separate row and the resumable runner still works."""
from __future__ import annotations

import random
from collections import defaultdict

from .config import persona_prompt, read_text
from .llm import LLM


def base_id(row: dict) -> str:
    """The prompt a row belongs to, for first attempts and retries alike."""
    return row.get("base_id", row["id"])


def transcript(messages: list[dict]) -> str:
    return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)


async def simulate_user(user_sim: LLM, sim_system: str, messages: list[dict]) -> str:
    """The simulated user's next message, given the conversation so far."""
    sim_messages = [
        {"role": "system", "content": sim_system},
        {"role": "user", "content": "Conversation so far:\n\n" + transcript(messages)
         + "\n\nWrite the user's next message."},
    ]
    # The simulated user occasionally returns nothing; one retry usually fixes it.
    follow_up = await user_sim.chat(sim_messages) or await user_sim.chat(sim_messages)
    if not follow_up:
        raise ValueError("empty simulated user message")
    return follow_up.strip().strip('"')


def retry_items(judged: list[dict], max_retries: int) -> list[dict]:
    """Prompts to try again: every attempt so far was rejected, and retries remain."""
    groups = defaultdict(list)
    for r in judged:
        groups[base_id(r)].append(r)
    items = []
    for prompt_id, rows in groups.items():
        if any(r["keep"] for r in rows) or len(rows) > max_retries:
            continue
        first = rows[0]
        items.append({
            "id": f"{prompt_id}#r{len(rows)}", "base_id": prompt_id, "kind": first["kind"],
            "text": first["messages"][0]["content"], "topic": first["topic"], "sensitive": first["sensitive"],
        })
    return items


class Generator:
    def __init__(self, cfg: dict, teacher_role: str = "teacher"):
        self.cfg = cfg
        self.persona = persona_prompt()
        self.teacher = LLM(cfg, teacher_role)
        self.sim_system = read_text("prompts/user_sim.md")
        self._user_sim: LLM | None = None

    @property
    def user_sim(self) -> LLM:
        if self._user_sim is None:  # only multi-turn generation needs it
            self._user_sim = LLM(self.cfg, "user_sim")
        return self._user_sim

    def usage_lines(self) -> list[str]:
        return [llm.usage_line() for llm in (self._user_sim, self.teacher) if llm]

    async def reply(self, conversation: list[dict]) -> str:
        # A half-finished reply would teach the student to stop mid-sentence, so it fails the item.
        text = await self.teacher.chat([{"role": "system", "content": self.persona}, *conversation],
                                       require_complete=True)
        if not text:
            raise ValueError("empty teacher reply")
        return text

    def record(self, p: dict, kind: str, messages: list[dict]) -> dict:
        rec = {
            "id": p["id"], "kind": kind, "topic": p["topic"], "sensitive": p["sensitive"],
            "teacher": self.teacher.model, "messages": messages,
        }
        if "base_id" in p:
            rec["base_id"] = p["base_id"]
        return rec

    async def single(self, p: dict) -> dict:
        messages = [{"role": "user", "content": p["text"]}]
        messages.append({"role": "assistant", "content": await self.reply(messages)})
        return self.record(p, "single", messages)

    async def multi(self, p: dict) -> dict:
        lo, hi = self.cfg["generate"]["multi_turns"]
        # Seeded by the prompt, so every attempt at a prompt has the same number of turns.
        n_turns = random.Random(f"{self.cfg['split']['seed']}-{base_id(p)}").randint(lo, hi)
        messages = [{"role": "user", "content": p["text"]}]
        for turn in range(n_turns):
            messages.append({"role": "assistant", "content": await self.reply(messages)})
            if turn < n_turns - 1:
                follow_up = await simulate_user(self.user_sim, self.sim_system, messages)
                messages.append({"role": "user", "content": follow_up})
        return self.record(p, "multi", messages)

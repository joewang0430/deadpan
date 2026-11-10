"""Step 02: label every prompt with a topic, a sensitivity flag and a suitability flag.

Uses the `tagger` role (a cheap API model; the smoke config uses local Ollama instead).
Writes <data_dir>/tags.jsonl. Safe to interrupt and rerun: finished ids are skipped."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from persona_lora.config import data_dir, load_config, read_text
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.llm import LLM, run_resumable
from persona_lora.text_utils import extract_json, to_bool


def build_system_prompt(cfg: dict) -> str:
    topics = cfg["topics"]
    return (
        read_text("prompts/tagger.md")
        .replace("{topics}", json.dumps(list(topics)))
        .replace("{topic_definitions}", "\n".join(f"- {k}: {v}" for k, v in topics.items()))
    )


async def run(cfg: dict, limit: int | None) -> None:
    en = read_jsonl(data_dir(cfg) / "prompts_en.jsonl")
    zh = read_jsonl(data_dir(cfg) / "prompts_zh.jsonl")
    if not en:
        raise SystemExit("No prompts found. Run scripts/01_collect_prompts.py first.")
    if limit:
        en, zh = en[:limit], zh[: max(1, limit // 4)]
    prompts = en + zh
    llm = LLM(cfg, "tagger")
    system = build_system_prompt(cfg)
    valid_topics = set(cfg["topics"])

    async def worker(p: dict) -> dict:
        # Wrap the text so the tagger labels it instead of answering it.
        user = (
            "Message to label:\n<<<\n" + p["text"] + "\n>>>\n\n"
            "Return the JSON labels for this message."
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(2):
            raw = await llm.chat(messages, temperature=0.0 if attempt == 0 else 0.5)
            obj = extract_json(raw) or {}
            topic = obj.get("topic")
            sensitive, suitable = to_bool(obj.get("sensitive")), to_bool(obj.get("suitable"))
            if topic in valid_topics and sensitive is not None and suitable is not None:
                return {"id": p["id"], "topic": topic, "sensitive": sensitive, "suitable": suitable}
        return {"id": p["id"], "topic": None, "sensitive": None, "suitable": False, "parse_error": True}

    out = data_dir(cfg) / "tags.jsonl"
    await run_resumable(prompts, worker, out, desc="tagging")
    print(llm.usage_line())

    tags = read_jsonl(out)
    print("topics:", Counter(t["topic"] for t in tags).most_common())
    print("suitable:", Counter(t["suitable"] for t in tags))
    print("sensitive:", Counter(t["sensitive"] for t in tags))
    print("parse errors:", sum(1 for t in tags if t.get("parse_error")))


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=None, help="only tag the first N English (and N/4 Chinese) prompts")
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config), args.limit))


if __name__ == "__main__":
    main()

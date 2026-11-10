"""Step 07 (evaluation): write the "pivot" test set.

Each conversation has three scripted user turns: an ordinary question, a follow-up, then
the person reveals a hard situation (one of the categories in config `sensitive`). The
assistant turns are generated later by each system under test, so all three systems see
exactly the same user turns. Training data almost never has this shape: a sensitive turn
in the middle of an ordinary conversation.

Writes <eval dir>/pivot_raw.jsonl (one row per LLM call; rerun to resume) and
<eval dir>/pivot.jsonl."""
from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter

from persona_lora.config import load_config, read_text
from persona_lora.evaluation import eval_dir
from persona_lora.io_utils import read_jsonl, utf8_console, write_jsonl
from persona_lora.llm import LLM, run_resumable
from persona_lora.text_utils import extract_json, jaccard, word_set

PER_CALL = 3
NEAR_DUPLICATE = 0.6  # word-set Jaccard similarity of whole conversations


def make_batches(cfg: dict) -> list[dict]:
    calls = math.ceil(cfg["eval"]["pivot_per_category"] / PER_CALL)
    return [{"id": f"{c}-{i}", "category": c} for c in cfg["sensitive"]["categories"] for i in range(calls)]


def select(cfg: dict, rows: list[dict]) -> list[dict]:
    per_category = cfg["eval"]["pivot_per_category"]
    kept, seen, counts = [], [], Counter()
    for r in sorted(rows, key=lambda r: r["id"]):  # file order depends on completion order
        for j, turns in enumerate(r["conversations"]):
            words = word_set(" ".join(turns))
            if counts[r["category"]] >= per_category or any(jaccard(words, w) >= NEAR_DUPLICATE for w in seen):
                continue
            seen.append(words)
            counts[r["category"]] += 1
            kept.append({"id": f"pivot-{r['id']}-{j}", "category": r["category"], "turns": turns,
                         "source": f"synthetic:{r['model']}"})
    return kept


async def run(cfg: dict) -> None:
    categories = cfg["sensitive"]["categories"]
    template = read_text("prompts/pivot_writer.md")
    llm = LLM(cfg, "prompt_writer")

    async def worker(b: dict) -> dict:
        prompt = (template.replace("{category}", b["category"].replace("_", " "))
                  .replace("{description}", categories[b["category"]]["description"])
                  .replace("{n}", str(PER_CALL)))
        for _ in range(2):
            obj = extract_json(await llm.chat([{"role": "user", "content": prompt}])) or {}
            convs = [[t.strip() for t in c] for c in obj.get("conversations", [])
                     if isinstance(c, list) and len(c) == 3 and all(isinstance(t, str) and t.strip() for t in c)]
            if convs:
                return {"id": b["id"], "category": b["category"], "model": llm.model, "conversations": convs}
        raise ValueError("no well-formed conversations in the reply")

    raw_path = eval_dir(cfg) / "pivot_raw.jsonl"
    await run_resumable(make_batches(cfg), worker, raw_path, desc="writing pivot conversations")
    print(llm.usage_line())

    kept = select(cfg, read_jsonl(raw_path))
    write_jsonl(eval_dir(cfg) / "pivot.jsonl", kept)
    print(f"kept {len(kept)} conversations")
    for category, n in Counter(p["category"] for p in kept).most_common():
        print(f"  {category:15s} {n}")


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config)))


if __name__ == "__main__":
    main()

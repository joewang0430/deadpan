"""Step 02b: write extra sensitive prompts (grief, crisis, emergencies) with an LLM.

Real sensitive prompts are rare in oasst2 (under 1% of usable English prompts): too
few to teach the student when to drop the sarcasm, and too few to test whether it
does. This step asks the `prompt_writer` role for user messages in each category of
config `sensitive.categories`. Step 03 puts some of them into train_single and the
rest into the test_sensitive set.

Writes <data_dir>/sensitive_raw.jsonl (one row per LLM call; rerun to resume) and
<data_dir>/prompts_sensitive.jsonl (the deduplicated prompts)."""
from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter

from persona_lora.config import data_dir, load_config, read_text
from persona_lora.io_utils import read_jsonl, utf8_console, write_jsonl
from persona_lora.llm import LLM, run_resumable
from persona_lora.text_utils import extract_json, jaccard, word_set

# Cycled across the calls of each category, so batches differ in voice as well as content.
STYLES = [
    "mixed, as described above",
    "most messages are short and casual, typed quickly on a phone",
    "most messages give some background before getting to the point",
    "most messages are understated: the person sounds calm or matter-of-fact, but the situation is clearly serious",
    "most messages lead with a practical question, and the hard situation comes up as context",
]
NEAR_DUPLICATE = 0.6  # word-set Jaccard similarity above which a message counts as a repeat


def make_batches(cfg: dict) -> list[dict]:
    s = cfg["sensitive"]
    batches = []
    for category, spec in s["categories"].items():
        for i in range(math.ceil(spec["n"] / s["per_call"])):
            batches.append({"id": f"{category}-{i:02d}", "category": category,
                            "n": s["per_call"], "style": STYLES[i % len(STYLES)]})
    return batches


def dedupe(cfg: dict, rows: list[dict]) -> tuple[list[dict], Counter]:
    categories = cfg["sensitive"]["categories"]
    lo, hi = cfg["collect"]["min_chars"]["en"], cfg["collect"]["max_chars"]["en"]
    kept, seen, dropped = [], [], Counter()
    for r in sorted(rows, key=lambda r: r["id"]):  # file order depends on completion order
        if r["category"] not in categories:
            continue
        for j, text in enumerate(r["texts"]):
            if not lo <= len(text) <= hi:
                dropped["length"] += 1
                continue
            words = word_set(text)
            if any(jaccard(words, w) >= NEAR_DUPLICATE for w in seen):
                dropped["near_duplicate"] += 1
                continue
            seen.append(words)
            kept.append({
                "id": f"syn-{r['id']}-{j}", "lang": "en", "text": text,
                "source": f"synthetic:{r['model']}", "category": r["category"],
                "topic": categories[r["category"]]["topic"], "sensitive": True,
            })
    return kept, dropped


async def run(cfg: dict) -> None:
    categories = cfg["sensitive"]["categories"]
    template = read_text("prompts/sensitive_writer.md")
    llm = LLM(cfg, "prompt_writer")

    async def worker(b: dict) -> dict:
        prompt = (
            template.replace("{category}", b["category"])
            .replace("{description}", categories[b["category"]]["description"])
            .replace("{n}", str(b["n"]))
            .replace("{style}", b["style"])
        )
        for _ in range(2):
            obj = extract_json(await llm.chat([{"role": "user", "content": prompt}])) or {}
            texts = [t.strip() for t in obj.get("messages", []) if isinstance(t, str) and t.strip()]
            if texts:
                return {"id": b["id"], "category": b["category"], "style": b["style"],
                        "model": llm.model, "texts": texts}
        raise ValueError("no messages in the reply")

    raw_path = data_dir(cfg) / "sensitive_raw.jsonl"
    await run_resumable(make_batches(cfg), worker, raw_path, desc="writing sensitive prompts")
    print(llm.usage_line())

    kept, dropped = dedupe(cfg, read_jsonl(raw_path))
    write_jsonl(data_dir(cfg) / "prompts_sensitive.jsonl", kept)
    print(f"kept {len(kept)} prompts; dropped {dict(dropped)}")
    for category, n in Counter(p["category"] for p in kept).most_common():
        print(f"  {category:15s} {n}")
    need = cfg["split"]["train_sensitive"] + cfg["split"]["test_sensitive"]
    if len(kept) < need:
        print(f"warning: step 03 needs {need}; raise the counts in config sensitive.categories and rerun")


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config)))


if __name__ == "__main__":
    main()

"""Step 05b: give rejected prompts another attempt (rejection sampling).

For every prompt whose conversations were all rejected in step 05, the same teacher
writes a fresh conversation, and the judge grades it like any other. Each prompt gets
at most `generate.retries` extra attempts. Using the same teacher keeps one voice in
the training data; prompts that keep failing are dropped, which is usually right
(an unanswerable or badly posed prompt).

Retry rows are appended to the same single/multi/judged files with ids like
"oa-...#r1". Step 06 keeps at most one conversation per prompt. Rerun to resume."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict

from persona_lora.config import gen_dir, load_config
from persona_lora.generate import Generator, base_id, retry_items
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.judge import grade_conversation, judge_system_prompt
from persona_lora.llm import LLM, run_resumable


async def run(cfg: dict, teacher_role: str) -> None:
    d = gen_dir(cfg) / teacher_role
    gen_paths = {"single": d / "single.jsonl", "multi": d / "multi.jsonl"}
    judged_path = d / "judged.jsonl"
    if not read_jsonl(judged_path):
        raise SystemExit(f"Nothing judged in {d}. Run scripts/05_judge.py first.")
    gen = Generator(cfg, teacher_role)
    judge = LLM(cfg, "judge")
    system = judge_system_prompt()
    max_retries = cfg["generate"]["retries"]

    async def grade_pending() -> None:
        convs = read_jsonl(gen_paths["single"]) + read_jsonl(gen_paths["multi"])
        await run_resumable(convs, lambda c: grade_conversation(judge, system, c, cfg["filter"]),
                            judged_path, desc="judging")

    # Each round adds at most one attempt per prompt, so max_retries rounds are enough.
    for rnd in range(1, max_retries + 1):
        await grade_pending()
        todo = retry_items(read_jsonl(judged_path), max_retries)
        if not todo:
            break
        print(f"round {rnd}: {len(todo)} prompts to retry")
        for kind, path in gen_paths.items():
            items = [p for p in todo if p["kind"] == kind]
            if items:
                await run_resumable(items, getattr(gen, kind), path, desc=f"retry {kind}")
    await grade_pending()

    groups = defaultdict(list)
    for r in read_jsonl(judged_path):
        groups[base_id(r)].append(r)
    for kind in ("single", "multi"):
        prompts = [rows for rows in groups.values() if rows[0]["kind"] == kind]
        if not prompts:
            continue
        first = sum(any(r["keep"] for r in rows if "base_id" not in r) for rows in prompts)
        kept = sum(any(r["keep"] for r in rows) for rows in prompts)
        print(f"{kind}: kept {kept}/{len(prompts)} prompts ({kept / len(prompts):.0%}); "
              f"{first} on the first attempt, {kept - first} recovered by retries")
    attempts = Counter(len(rows) for rows in groups.values())
    print("attempts per prompt:", dict(sorted(attempts.items())))
    for line in [*gen.usage_lines(), judge.usage_line()]:
        print(line)


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--teacher-role", default="teacher")
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config), args.teacher_role))


if __name__ == "__main__":
    main()

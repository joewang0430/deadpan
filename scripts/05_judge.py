"""Step 05: grade every generated assistant turn with the judge, decide what to keep.

A conversation is kept only if EVERY assistant turn passes:
  - helpfulness >= filter.min_helpfulness, no factual error, no insult to the user
  - tone appropriate (no sarcasm in sensitive contexts)
  - style >= filter.min_style, unless the context is sensitive
  - no exclamation marks, emojis, banned cliches or claims to be another model

Writes <gen_dir>/<teacher_role>/judged.jsonl. Rerun to resume.
The statistics printed here count first attempts only; step 05b reports retries."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from persona_lora.config import gen_dir, load_config
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.judge import grade_conversation, judge_system_prompt
from persona_lora.llm import LLM, run_resumable


async def run(cfg: dict, teacher_role: str, limit: int | None) -> None:
    in_dir = gen_dir(cfg) / teacher_role
    convs = read_jsonl(in_dir / "single.jsonl")[:limit] + read_jsonl(in_dir / "multi.jsonl")[:limit]
    if not convs:
        raise SystemExit(f"No generations in {in_dir}. Run scripts/04_generate.py first.")
    judge = LLM(cfg, "judge")
    system = judge_system_prompt()

    out = in_dir / "judged.jsonl"
    await run_resumable(convs, lambda c: grade_conversation(judge, system, c, cfg["filter"]),
                        out, desc="judging")
    print(judge.usage_line())

    rows = [r for r in read_jsonl(out) if "base_id" not in r]
    for kind in ("single", "multi"):
        sub = [r for r in rows if r["kind"] == kind]
        if sub:
            kept = sum(r["keep"] for r in sub)
            print(f"{kind}: kept {kept}/{len(sub)} ({kept / len(sub):.0%})")
    print("reject reasons:", Counter(x for r in rows for x in r["reject_reasons"]).most_common())
    turns = [v for r in rows for v in r["verdicts"] if not v["sensitive_context"]]
    if turns:
        print(f"mean style={sum(v['style'] for v in turns) / len(turns):.2f} "
              f"mean helpfulness={sum(v['helpfulness'] for v in turns) / len(turns):.2f} "
              f"(non-sensitive turns, n={len(turns)})")


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--teacher-role", default="teacher")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config), args.teacher_role, args.limit))


if __name__ == "__main__":
    main()

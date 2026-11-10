"""Step 04: generate persona conversations with a teacher model.

The teacher sees the persona system prompt; the saved conversations do NOT include
it. Training on these teaches the student to behave in persona without the prompt
(context distillation).

  single: one user prompt -> one persona reply
  multi:  a seed prompt, then a simulated user keeps the conversation going

Writes <gen_dir>/<teacher_role>/{single,multi}.jsonl. Rerun to resume.
Prompts rejected in step 05 get more attempts in step 05b."""
from __future__ import annotations

import argparse
import asyncio

from persona_lora.config import data_dir, gen_dir, load_config
from persona_lora.generate import Generator
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.llm import run_resumable


async def run(cfg: dict, mode: str, teacher_role: str, limit: int | None) -> None:
    splits = data_dir(cfg) / "splits"
    out_dir = gen_dir(cfg) / teacher_role
    gen = Generator(cfg, teacher_role)

    if mode in ("single", "both"):
        prompts = read_jsonl(splits / "train_single.jsonl")[:limit]
        await run_resumable(prompts, gen.single, out_dir / "single.jsonl", desc="single-turn")
    if mode in ("multi", "both"):
        prompts = read_jsonl(splits / "train_multi.jsonl")[:limit]
        await run_resumable(prompts, gen.multi, out_dir / "multi.jsonl", desc="multi-turn")
    for line in gen.usage_lines():
        print(line)


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--mode", choices=["single", "multi", "both"], default="both")
    ap.add_argument("--teacher-role", default="teacher",
                    help="role from the config; use teacher_self for the self-distillation control")
    ap.add_argument("--limit", type=int, default=None, help="only use the first N prompts of each split")
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config), args.mode, args.teacher_role, args.limit))


if __name__ == "__main__":
    main()

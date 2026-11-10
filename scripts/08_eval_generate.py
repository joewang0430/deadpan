"""Step 08 (evaluation): every system under test answers every test set.

  seen, unseen, sensitive   one user prompt, one reply
  pivot                     three scripted user turns (step 07), three replies
  multiturn                 a seed prompt, then the simulated user continues for
                            config eval.multiturn_turns replies (drift test)
  ifeval                    one IFEval prompt, one reply (scored by step 10)

Writes <eval dir>/gen/<system>/<set>.jsonl. Saved conversations never include the
persona system prompt, so the judges cannot tell which system wrote them. Rerun to resume.

  uv run python scripts/08_eval_generate.py                          # everything
  uv run python scripts/08_eval_generate.py --systems base --sets seen --limit 5"""
from __future__ import annotations

import argparse
import asyncio

from persona_lora.config import load_config
from persona_lora.evaluation import ALL_SETS, Runner, System, eval_dir, load_set
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.llm import run_resumable


async def run(cfg: dict, systems: list[str], sets: list[str], limit: int | None) -> None:
    runner = Runner(cfg)
    items = {s: load_set(cfg, s)[:limit] for s in sets}
    for name in systems:
        system = System(cfg, name)
        for s in sets:
            out = eval_dir(cfg) / "gen" / name / f"{s}.jsonl"
            await run_resumable(items[s], lambda item, s=s: runner.run(system, s, item), out, desc=f"{name}/{s}")
        print(system.llm.usage_line())
    if runner._user_sim:
        print(runner.user_sim.usage_line())

    print("\nreplies per system and set (truncated at max_tokens in brackets):")
    for name in systems:
        cells = []
        for s in sets:
            rows = read_jsonl(eval_dir(cfg) / "gen" / name / f"{s}.jsonl")
            cut = sum(sum(r["truncated"]) for r in rows)
            cells.append(f"{s}={len(rows)}" + (f" [{cut}]" if cut else ""))
        print(f"  {name:8s} " + "  ".join(cells))


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--systems", default=None, help="comma-separated; default: all in config eval.systems")
    ap.add_argument("--sets", default=",".join(ALL_SETS), help="comma-separated test sets")
    ap.add_argument("--limit", type=int, default=None, help="only the first N items of each set")
    args = ap.parse_args()
    cfg = load_config(args.config)
    systems = args.systems.split(",") if args.systems else list(cfg["eval"]["systems"])
    asyncio.run(run(cfg, systems, args.sets.split(","), args.limit))


if __name__ == "__main__":
    main()

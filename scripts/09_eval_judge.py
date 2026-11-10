"""Step 09 (evaluation): the judges grade every assistant turn from step 08.

Each judge in config eval.judges (default: GLM-5.3 and a second judge from another model
family) grades every assistant turn with prompts/judge.md, seeing the conversation up to
that turn. The conversations carry no system prompt, so the judges do not know which
system wrote them. Ends with a summary per judge, set and system.

Writes <eval dir>/judged/<judge role>/<system>/<set>.jsonl. Rerun to resume."""
from __future__ import annotations

import argparse
import asyncio

from persona_lora.config import load_config
from persona_lora.evaluation import JUDGED_SETS, eval_dir, item_scores, summarize
from persona_lora.io_utils import read_jsonl, utf8_console
from persona_lora.judge import judge_conversation, judge_system_prompt
from persona_lora.llm import LLM, run_resumable


async def run(cfg: dict, judges: list[str], systems: list[str], sets: list[str]) -> None:
    system_prompt = judge_system_prompt()
    for role in judges:
        llm = LLM(cfg, role)

        async def worker(row: dict) -> dict:
            verdicts = await judge_conversation(llm, system_prompt, row["messages"])
            meta = {k: v for k, v in row.items() if k not in ("messages", "truncated", "model")}
            return {**meta, "judge": llm.model, "verdicts": verdicts}

        for name in systems:
            for s in sets:
                rows = read_jsonl(eval_dir(cfg) / "gen" / name / f"{s}.jsonl")
                if rows:
                    out = eval_dir(cfg) / "judged" / role / name / f"{s}.jsonl"
                    await run_resumable(rows, worker, out, desc=f"{role}: {name}/{s}")
        print(llm.usage_line())

    for role in judges:
        print(f"\n== {role} ({cfg['roles'][role]['model']}) ==")
        for s in sets:
            for name in systems:
                rows = read_jsonl(eval_dir(cfg) / "judged" / role / name / f"{s}.jsonl")
                if rows:
                    summary = summarize([item_scores(s, r, r["verdicts"]) for r in rows])
                    cells = "  ".join(f"{k}={v:.2f}" for k, v in summary.items())
                    print(f"  {s:9s} {name:7s} n={len(rows):3d}  {cells}")


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--judges", default=None, help="comma-separated roles; default: config eval.judges")
    ap.add_argument("--systems", default=None, help="comma-separated; default: all in config eval.systems")
    ap.add_argument("--sets", default=",".join(JUDGED_SETS))
    args = ap.parse_args()
    cfg = load_config(args.config)
    judges = args.judges.split(",") if args.judges else cfg["eval"]["judges"]
    systems = args.systems.split(",") if args.systems else list(cfg["eval"]["systems"])
    asyncio.run(run(cfg, judges, systems, args.sets.split(",")))


if __name__ == "__main__":
    main()

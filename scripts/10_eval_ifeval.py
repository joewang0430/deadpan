"""Step 10 (evaluation): score the IFEval replies from step 08 with Google's checkers.

Reports the four standard IFEval numbers (prompt- and instruction-level, strict and loose),
and instruction-level strict accuracy split by whether the instruction conflicts with the
persona's default length and format (persona_lora/ifeval/__init__.py). Lower accuracy only
on the conflict split means the style overrides the user; lower accuracy on the
no-conflict split means the fine-tuning damaged instruction following itself.

Writes <eval dir>/ifeval_scores/<system>.jsonl. Needs no API calls."""
from __future__ import annotations

import argparse
from statistics import mean

from persona_lora.config import load_config
from persona_lora.evaluation import eval_dir, load_ifeval
from persona_lora.ifeval import follows
from persona_lora.io_utils import read_jsonl, utf8_console, write_jsonl


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--systems", default=None, help="comma-separated; default: all in config eval.systems")
    args = ap.parse_args()
    cfg = load_config(args.config)
    systems = args.systems.split(",") if args.systems else list(cfg["eval"]["systems"])
    items = {r["id"]: r for r in load_ifeval(cfg)}

    print(f"{'system':8s} {'n':>4s} {'prompt_strict':>14s} {'prompt_loose':>13s} {'inst_strict':>12s} "
          f"{'inst_loose':>11s} {'conflict':>9s} {'no_conflict':>12s}")
    for name in systems:
        rows = read_jsonl(eval_dir(cfg) / "gen" / name / "ifeval.jsonl")
        if not rows:
            continue
        scored = [{"id": r["id"], "truncated": r["truncated"][0],
                   "results": follows(items[r["id"]], r["messages"][-1]["content"])} for r in rows]
        write_jsonl(eval_dir(cfg) / "ifeval_scores" / f"{name}.jsonl", scored)
        inst = [x for s in scored for x in s["results"]]
        conflict = [x["strict"] for x in inst if x["conflict"]]
        other = [x["strict"] for x in inst if not x["conflict"]]
        print(f"{name:8s} {len(scored):4d} "
              f"{mean(all(x['strict'] for x in s['results']) for s in scored):14.3f} "
              f"{mean(all(x['loose'] for x in s['results']) for s in scored):13.3f} "
              f"{mean(x['strict'] for x in inst):12.3f} {mean(x['loose'] for x in inst):11.3f} "
              f"{mean(conflict) if conflict else float('nan'):9.3f} {mean(other) if other else float('nan'):12.3f}")


if __name__ == "__main__":
    main()

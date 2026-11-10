"""Pilot: compare candidate teacher models side by side before generating the full dataset.

Each candidate in config `pilot.teachers` answers the same few training prompts with
the persona prompt. With --judge, every reply is also graded. Read the resulting
markdown file and pick the teacher whose replies you like best.

Writes <gen_dir>/pilot/teachers.md and teachers.jsonl."""
from __future__ import annotations

import argparse
import asyncio

from persona_lora.config import data_dir, gen_dir, load_config, persona_prompt
from persona_lora.io_utils import read_jsonl, utf8_console, write_jsonl
from persona_lora.judge import judge_system_prompt, judge_turn
from persona_lora.llm import LLM

DEFAULTS = {"endpoint": "openrouter", "temperature": 0.8, "max_tokens": 900, "concurrency": 8}


def pick_prompts(cfg: dict, n: int) -> list[dict]:
    pool = read_jsonl(data_dir(cfg) / "splits" / "train_single.jsonl")
    if not pool:
        raise SystemExit("No training prompts. Run steps 01-03 first.")
    chosen = pool[:n]
    # Make sure the pilot includes one sensitive prompt, to check that sarcasm is dropped.
    if not any(p["sensitive"] for p in chosen):
        sensitive = next((p for p in pool[n:] if p["sensitive"]), None)
        if sensitive:
            chosen[-1] = sensitive
    return chosen


def summary_table(teachers: list[LLM], prompts: list[dict], by_key: dict) -> list[str]:
    lines = [
        "| teacher | errors | mean words | mean style | mean helpfulness | completion tokens |",
        "|---|---|---|---|---|---|",
    ]
    for t in teachers:
        rs = [by_key[(t.model, p["id"])] for p in prompts]
        ok = [r for r in rs if not r.get("error")]
        words = f"{sum(len(r['reply'].split()) for r in ok) / len(ok):.0f}" if ok else "-"
        vs = [r["verdict"] for r in ok if r["verdict"] and not r["verdict"]["sensitive_context"]]
        style = f"{sum(v['style'] for v in vs) / len(vs):.2f}" if vs else "-"
        helpf = f"{sum(v['helpfulness'] for v in vs) / len(vs):.2f}" if vs else "-"
        lines.append(f"| {t.model} | {len(rs) - len(ok)} | {words} | {style} | {helpf} | {t.completion_tokens} |")
    return lines


async def run(cfg: dict, use_judge: bool) -> None:
    prompts = pick_prompts(cfg, cfg["pilot"]["n_prompts"])
    persona = persona_prompt()
    teachers = [LLM(cfg, spec={**DEFAULTS, **t}) for t in cfg["pilot"]["teachers"]]
    judge = LLM(cfg, "judge") if use_judge else None
    jsys = judge_system_prompt() if use_judge else None

    async def one(t: LLM, p: dict) -> dict:
        messages = [{"role": "user", "content": p["text"]}]
        try:
            reply = await t.chat([{"role": "system", "content": persona}, *messages])
        except Exception as e:
            print(f"{t.model} failed on {p['id']}: {e}")
            return {"teacher": t.model, "id": p["id"], "reply": f"(error: {e})", "verdict": None, "error": True}
        verdict = None
        if judge and reply:
            try:
                verdict = await judge_turn(judge, jsys, [*messages, {"role": "assistant", "content": reply}])
            except Exception as e:
                print(f"judge failed for {t.model}/{p['id']}: {e}")
        return {"teacher": t.model, "id": p["id"], "reply": reply, "verdict": verdict}

    results = await asyncio.gather(*(one(t, p) for t in teachers for p in prompts))
    out_dir = gen_dir(cfg) / "pilot"
    write_jsonl(out_dir / "teachers.jsonl", results)

    by_key = {(r["teacher"], r["id"]): r for r in results}
    lines = ["# Teacher pilot", "", *summary_table(teachers, prompts, by_key)]
    for i, p in enumerate(prompts, 1):
        tag = p["topic"] + (", sensitive" if p["sensitive"] else "")
        quoted = "> " + p["text"].replace("\n", "\n> ")
        lines += ["", "---", "", f"## Prompt {i} ({tag})", "", quoted]
        for t in teachers:
            r = by_key[(t.model, p["id"])]
            v = r["verdict"]
            score = f" (style {v['style']}, helpfulness {v['helpfulness']})" if v else ""
            lines += ["", f"### {t.model}{score}", "", r["reply"]]
    (out_dir / "teachers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_dir / 'teachers.md'}")
    for t in teachers:
        print(t.usage_line())
    if judge:
        print(judge.usage_line())


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--judge", action="store_true", help="also grade every reply with the judge")
    args = ap.parse_args()
    asyncio.run(run(load_config(args.config), args.judge))


if __name__ == "__main__":
    main()

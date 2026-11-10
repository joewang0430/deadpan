"""Step 06: build the final SFT files from kept conversations.

Output rows are {"id", "kind", "messages"} with NO system message: the student
must learn the persona without being told. At most one conversation per prompt
(retries from step 05b only exist for prompts whose earlier attempts failed).
Writes <gen_dir>/<teacher_role>/sft/{train,val}.jsonl and stats.json."""
from __future__ import annotations

import argparse
import random
from collections import Counter

from persona_lora.config import gen_dir, load_config
from persona_lora.generate import base_id
from persona_lora.io_utils import read_jsonl, utf8_console, write_json, write_jsonl
from persona_lora.text_utils import looks_truncated


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--teacher-role", default="teacher")
    args = ap.parse_args()
    cfg = load_config(args.config)
    in_dir = gen_dir(cfg) / args.teacher_role

    by_prompt: dict[str, dict] = {}
    for r in read_jsonl(in_dir / "judged.jsonl"):
        if r["keep"]:
            by_prompt.setdefault(base_id(r), r)
    # The judge does not notice replies cut off at max_tokens; drop those conversations here.
    truncated = sorted(k for k, r in by_prompt.items()
                       if any(looks_truncated(m["content"]) for m in r["messages"] if m["role"] == "assistant"))
    rows = [r for k, r in by_prompt.items() if k not in truncated]
    if not rows:
        raise SystemExit("No kept conversations. Run scripts/05_judge.py first.")
    for r in rows:
        assert all(m["role"] in ("user", "assistant") for m in r["messages"]), "unexpected role"
        assert r["messages"][-1]["role"] == "assistant"

    rng = random.Random(cfg["split"]["seed"])
    rng.shuffle(rows)
    n_val = max(1, round(len(rows) * cfg["filter"]["val_fraction"]))
    val, train = rows[:n_val], rows[n_val:]

    def slim(r: dict) -> dict:
        return {"id": base_id(r), "kind": r["kind"], "messages": r["messages"]}

    out = in_dir / "sft"
    write_jsonl(out / "train.jsonl", map(slim, train))
    write_jsonl(out / "val.jsonl", map(slim, val))

    assistant_words = [
        len(m["content"].split()) for r in rows for m in r["messages"] if m["role"] == "assistant"
    ]
    stats = {
        "train": len(train),
        "val": len(val),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "from_retries": sum(1 for r in rows if "base_id" in r),
        "dropped_truncated": truncated,
        "topics": dict(Counter(r["topic"] for r in rows).most_common()),
        "sensitive": sum(1 for r in rows if r["sensitive"]),
        "assistant_turns": len(assistant_words),
        "mean_assistant_words": round(sum(assistant_words) / len(assistant_words), 1),
    }
    write_json(out / "stats.json", stats)
    for k, v in stats.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()

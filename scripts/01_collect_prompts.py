"""Step 01: collect candidate user prompts (first user turns) from oasst2.

Writes <data_dir>/prompts_en.jsonl and <data_dir>/prompts_zh.jsonl.
Only the prompts are used; oasst2's own assistant replies are ignored."""
from __future__ import annotations

import argparse
import re
from collections import Counter

from datasets import concatenate_datasets, load_dataset

from persona_lora.config import data_dir, load_config
from persona_lora.io_utils import utf8_console, write_json, write_jsonl

LANGS = ("en", "zh")


def label_value(labels: dict | None, name: str) -> float | None:
    if not labels or name not in labels["name"]:
        return None
    return labels["value"][labels["name"].index(name)]


def dedupe_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    c = cfg["collect"]

    ds = load_dataset(c["dataset"])
    rows = concatenate_datasets([ds[split] for split in ds])
    roots = rows.filter(
        lambda r: r["parent_id"] is None and r["role"] == "prompter" and r["lang"] in LANGS
    )

    stats: Counter = Counter()
    out: dict[str, list[dict]] = {lang: [] for lang in LANGS}
    seen: set[str] = set()
    for r in roots:
        lang, text = r["lang"], r["text"].strip()
        stats[f"{lang}_roots"] += 1
        if r["deleted"] or not r["review_result"] or r["synthetic"]:
            stats[f"{lang}_drop_review"] += 1
            continue
        if any(
            (label_value(r["labels"], name) or 0.0) >= c["blocked_label_threshold"]
            for name in c["blocked_labels"]
        ):
            stats[f"{lang}_drop_label"] += 1
            continue
        if not c["min_chars"][lang] <= len(text) <= c["max_chars"][lang]:
            stats[f"{lang}_drop_length"] += 1
            continue
        key = dedupe_key(text)
        if key in seen:
            stats[f"{lang}_drop_duplicate"] += 1
            continue
        seen.add(key)
        out[lang].append(
            {
                "id": "oa-" + r["message_id"].replace("-", "")[:12],
                "lang": lang,
                "text": text,
                "source": c["dataset"],
            }
        )

    for lang in LANGS:
        stats[f"{lang}_kept"] = len(out[lang])
        write_jsonl(data_dir(cfg) / f"prompts_{lang}.jsonl", out[lang])
    write_json(data_dir(cfg) / "collect_stats.json", dict(stats))
    for k, v in sorted(stats.items()):
        print(f"{k:24s} {v}")


if __name__ == "__main__":
    main()

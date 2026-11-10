"""Step 03: split tagged prompts into training pools and test sets.

Held-out topics (config: split.heldout_topics) never appear in training, so the
test_unseen set measures whether the persona generalizes to new topics.
Synthetic sensitive prompts from step 02b go to test_sensitive and into train_single,
balanced across their categories.
Writes <data_dir>/splits/*.jsonl and <data_dir>/splits/stats.json."""
from __future__ import annotations

import argparse
import random
from collections import Counter, defaultdict

from persona_lora.config import data_dir, load_config
from persona_lora.io_utils import read_jsonl, utf8_console, write_json, write_jsonl


def take(pool: list[dict], n: int, name: str) -> list[dict]:
    if len(pool) < n:
        print(f"warning: {name} wants {n} prompts but only {len(pool)} are available")
    taken = pool[:n]
    del pool[:n]
    return taken


def round_robin(rows: list[dict], key: str, rng: random.Random) -> list[dict]:
    """Shuffle within each group, then deal one row per group in turn, so any prefix
    of the result is balanced across groups."""
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    for g in groups.values():
        rng.shuffle(g)
    longest = max((len(g) for g in groups.values()), default=0)
    return [groups[k][i] for i in range(longest) for k in sorted(groups) if i < len(groups[k])]


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    s = cfg["split"]
    ddir = data_dir(cfg)

    tags = {t["id"]: t for t in read_jsonl(ddir / "tags.jsonl")}
    if not tags:
        raise SystemExit("No tags found. Run scripts/02_tag_prompts.py first.")

    def usable(lang: str) -> list[dict]:
        rows = []
        for p in read_jsonl(ddir / f"prompts_{lang}.jsonl"):
            t = tags.get(p["id"])
            if t and t["suitable"] and not t.get("parse_error"):
                rows.append({**p, "topic": t["topic"], "sensitive": t["sensitive"]})
        return rows

    rng = random.Random(s["seed"])
    en, zh = usable("en"), usable("zh")
    n_en, n_zh = len(en), len(zh)  # count now: take() below consumes zh
    rng.shuffle(en)
    rng.shuffle(zh)

    heldout = set(s["heldout_topics"])
    unseen_pool = [p for p in en if p["topic"] in heldout]
    seen_pool = [p for p in en if p["topic"] not in heldout]

    half = s["test_multiturn"] // 2
    splits = {
        "test_unseen": take(unseen_pool, s["test_unseen"], "test_unseen"),
        "test_seen": take(seen_pool, s["test_seen"], "test_seen"),
    }
    splits["test_multiturn"] = take(seen_pool, s["test_multiturn"] - half, "test_multiturn/seen") + take(
        unseen_pool, half, "test_multiturn/unseen"
    )
    splits["test_zh"] = take(zh, s["test_zh"], "test_zh")

    synthetic = read_jsonl(ddir / "prompts_sensitive.jsonl")
    if s["test_sensitive"] + s["train_sensitive"] and not synthetic:
        raise SystemExit("No synthetic sensitive prompts. Run scripts/02b_sensitive_prompts.py first.")
    synthetic = round_robin(synthetic, "category", rng)
    splits["test_sensitive"] = take(synthetic, s["test_sensitive"], "test_sensitive")
    train_synthetic = take(synthetic, s["train_sensitive"], "train_sensitive")
    splits["train_single"] = (
        take(seen_pool, s["train_single"] - len(train_synthetic), "train_single") + train_synthetic
    )
    rng.shuffle(splits["train_single"])
    splits["train_multi"] = take(seen_pool, s["train_multi"], "train_multi")

    stats = {"usable_en": n_en, "usable_zh": n_zh, "unused_seen": len(seen_pool),
             "unused_unseen": len(unseen_pool), "unused_synthetic": len(synthetic), "splits": {}}
    for name, rows in splits.items():
        for r in rows:
            r["split"] = name
        write_jsonl(ddir / "splits" / f"{name}.jsonl", rows)
        stats["splits"][name] = {
            "n": len(rows),
            "sensitive": sum(1 for r in rows if r["sensitive"]),
            "synthetic": sum(1 for r in rows if r["source"].startswith("synthetic")),
            "topics": dict(Counter(r["topic"] for r in rows).most_common()),
        }
        st = stats["splits"][name]
        print(f"{name:15s} n={len(rows):5d} sensitive={st['sensitive']:4d} (synthetic {st['synthetic']})")

    # Sanity check: no prompt appears in more than one split, no held-out topic in training.
    all_ids = [r["id"] for rows in splits.values() for r in rows]
    assert len(all_ids) == len(set(all_ids)), "a prompt landed in two splits"
    for name in ("train_single", "train_multi"):
        assert not any(r["topic"] in heldout for r in splits[name]), "held-out topic leaked into training"
    write_json(ddir / "splits" / "stats.json", stats)
    print(f"usable en={n_en} zh={n_zh}; unused seen={len(seen_pool)} unseen={len(unseen_pool)} "
          f"synthetic={len(synthetic)}")


if __name__ == "__main__":
    main()

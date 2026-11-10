"""Quick look at the trained model: base vs LoRA on the same prompts, with sampling.

Greedy decoding (used by train_lora.py's samples) tends to hide stylistic choices,
because a dry remark is rarely the single most likely next token. This samples the
way people actually chat with the model. No system prompt for either model.

  python sample_check.py                       # writes sample_check.md
"""
from __future__ import annotations

import argparse
import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

PROMPTS = [
    "How do I boil an egg?",
    "What's the best way to learn a new language as an adult?",
    "Is it worth buying an electric car?",
    "Why does my phone battery die so fast?",
    "Can you recommend a good pasta recipe for a weeknight?",       # held-out topic (food)
    "my dad was just diagnosed with cancer and i don't know what to say to him",  # sensitive
]


@torch.no_grad()
def sample(path: str, n: int, max_new: int, seed: int) -> dict[str, list[str]]:
    tokenizer = AutoTokenizer.from_pretrained(path)
    gpu = torch.cuda.is_available()
    model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16 if gpu else torch.float32)
    model.to("cuda" if gpu else "cpu").eval()
    out = {}
    for p in PROMPTS:
        inputs = tokenizer.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True,
                                               return_dict=True, return_tensors="pt").to(model.device)
        torch.manual_seed(seed)
        # Qwen's recommended sampling settings for the Instruct-2507 models.
        gen = model.generate(**inputs, do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                             num_return_sequences=n, max_new_tokens=max_new, pad_token_id=tokenizer.pad_token_id)
        start = inputs["input_ids"].shape[1]
        out[p] = [tokenizer.decode(g[start:], skip_special_tokens=True).strip() for g in gen]
    del model
    torch.cuda.empty_cache()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="Qwen/Qwen3-4B-Instruct-2507")
    ap.add_argument("--lora", default="outputs/persona-lora/merged")
    ap.add_argument("--n", type=int, default=2, help="samples per prompt per model")
    ap.add_argument("--max-new", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="sample_check.md")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    results = {name: sample(path, args.n, args.max_new, args.seed)
               for name, path in (("base", args.base), ("lora", args.lora))}
    lines = ["# Base vs LoRA, sampled (temperature 0.7, top_p 0.8, no system prompt)", ""]
    for p in PROMPTS:
        lines += [f"## {p}", ""]
        for name in ("base", "lora"):
            for i, reply in enumerate(results[name][p], 1):
                lines += [f"### {name} #{i}", "", reply, ""]
    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()

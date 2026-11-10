"""Fine-tune Qwen3-4B-Instruct-2507 with LoRA on the persona SFT data.

Runs on one GPU (one H100/A100 80GB on RunPod). From the folder holding this file:

  pip install -r requirements-train.txt
  python train_lora.py 2>&1 | tee train.log

Inputs: train.jsonl and val.jsonl from step 06 (rows {"id", "kind", "messages"}).
Outputs in outputs/persona-lora/: adapter/ (the LoRA weights), merged/ (base + LoRA
in bf16, for evaluation and GGUF export), samples.md (replies to a few test
prompts), log_history.json, and run_config.json.

The loss is computed on assistant turns only (including the end-of-turn token),
and no system prompt is added, matching how the fine-tuned model will be used."""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

BASE_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
SAMPLE_PROMPTS = [
    "How do I boil an egg?",                                   # in the persona's own examples
    "What's the best way to learn a new language as an adult?",
    "Can you recommend a good pasta recipe for a weeknight?",  # held-out topic (food)
    "my dad was just diagnosed with cancer and i don't know what to say to him",  # sensitive
]


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def encode(tokenizer, messages: list[dict], max_len: int) -> dict | None:
    """Token ids for the whole conversation, with labels = -100 outside assistant turns."""
    full = tokenizer.apply_chat_template(messages, tokenize=False)
    spans = []  # character ranges of assistant replies, end-of-turn token included
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        before = tokenizer.apply_chat_template(messages[:i], tokenize=False, add_generation_prompt=True)
        upto = tokenizer.apply_chat_template(messages[: i + 1], tokenize=False)
        if not (full.startswith(upto) and upto.startswith(before)):
            raise ValueError("chat template is not prefix-consistent; masking would be wrong")
        spans.append((len(before), len(upto.rstrip("\n"))))
    enc = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    if len(ids) > max_len:
        return None
    labels = [
        tok if any(s <= start and end <= e for s, e in spans) and end > start else -100
        for tok, (start, end) in zip(ids, enc["offset_mapping"])
    ]
    return {"input_ids": ids, "labels": labels}


class Collator:
    def __init__(self, pad_id: int):
        self.pad_id = pad_id

    def __call__(self, batch: list[dict]) -> dict:
        n = max(len(b["input_ids"]) for b in batch)
        pad = lambda seq, v: seq + [v] * (n - len(seq))
        return {
            "input_ids": torch.tensor([pad(b["input_ids"], self.pad_id) for b in batch]),
            "labels": torch.tensor([pad(b["labels"], -100) for b in batch]),
            "attention_mask": torch.tensor([pad([1] * len(b["input_ids"]), 0) for b in batch]),
        }


def load_split(tokenizer, path: str, max_len: int) -> list[dict]:
    rows = read_jsonl(path)
    out = [e for e in (encode(tokenizer, r["messages"], max_len) for r in rows) if e]
    n_tok = sum(len(e["input_ids"]) for e in out)
    n_lab = sum(sum(1 for t in e["labels"] if t != -100) for e in out)
    print(f"{path}: {len(out)} conversations ({len(rows) - len(out)} dropped as longer than {max_len} tokens), "
          f"{n_tok} tokens, {n_lab} trained tokens")
    return out


@torch.no_grad()
def write_samples(model, tokenizer, path: Path) -> None:
    model.eval()
    lines = ["# Sample replies after training (no system prompt, greedy decoding)", ""]
    for p in SAMPLE_PROMPTS:
        ids = tokenizer.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True,
                                            return_tensors="pt").to(model.device)
        out = model.generate(ids, max_new_tokens=300, do_sample=False, pad_token_id=tokenizer.pad_token_id)
        reply = tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True).strip()
        lines += [f"## {p}", "", reply, ""]
        print(f"\n>>> {p}\n{reply}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", default="train.jsonl")
    ap.add_argument("--val", default="val.jsonl")
    ap.add_argument("--out", default="outputs/persona-lora")
    ap.add_argument("--model", default=BASE_MODEL)
    ap.add_argument("--max-len", type=int, default=4096)
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--batch", type=int, default=4, help="conversations per GPU step")
    ap.add_argument("--grad-accum", type=int, default=4, help="steps per optimizer update (effective batch = batch x this)")
    ap.add_argument("--max-steps", type=int, default=-1, help="stop early, for a quick test run")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-merge", action="store_true", help="skip saving the merged bf16 model")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    gpu = torch.cuda.is_available()
    print("GPU:", torch.cuda.get_device_name(0) if gpu else "none (CPU test run)")

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    train = load_split(tokenizer, args.train, args.max_len)
    val = load_split(tokenizer, args.val, args.max_len)

    model = AutoModelForCausalLM.from_pretrained(
        args.model, dtype=torch.bfloat16 if gpu else torch.float32, attn_implementation="sdpa"
    )
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05, target_modules=TARGET_MODULES,
        bias="none", task_type="CAUSAL_LM",
    ))
    model.print_trainable_parameters()

    targs = TrainingArguments(
        output_dir=str(out / "checkpoints"),
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        weight_decay=0.0,
        bf16=gpu,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=5,
        eval_strategy="steps",
        eval_steps=25,
        save_strategy="epoch",
        save_total_limit=2,
        report_to="none",
        remove_unused_columns=False,
        seed=args.seed,
        use_cpu=not gpu,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train, eval_dataset=val,
                      data_collator=Collator(tokenizer.pad_token_id))
    start = time.time()
    trainer.train()
    minutes = (time.time() - start) / 60
    final_eval = trainer.evaluate()
    print(f"training took {minutes:.1f} min; final val loss {final_eval['eval_loss']:.4f}")

    model.save_pretrained(out / "adapter")
    tokenizer.save_pretrained(out / "adapter")
    (out / "log_history.json").write_text(json.dumps(trainer.state.log_history, indent=2), encoding="utf-8")
    (out / "run_config.json").write_text(json.dumps({
        **vars(args), "target_modules": TARGET_MODULES, "lora_dropout": 0.05,
        "effective_batch": args.batch * args.grad_accum, "train_conversations": len(train),
        "val_conversations": len(val), "minutes": round(minutes, 1),
        "final_eval_loss": final_eval["eval_loss"], "gpu": torch.cuda.get_device_name(0) if gpu else None,
    }, indent=2), encoding="utf-8")

    write_samples(model, tokenizer, out / "samples.md")
    if not args.no_merge:
        merged = model.merge_and_unload()
        merged.save_pretrained(out / "merged", safe_serialization=True)
        tokenizer.save_pretrained(out / "merged")
        print(f"merged model saved to {out / 'merged'}")
    print(f"done; outputs in {out}")


if __name__ == "__main__":
    main()

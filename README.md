# Deadpan

**Persona in the weights vs. in the prompt.** Teaching a small local LLM
(Qwen3-4B-Instruct-2507) a deadpan, blunt, sarcastic persona with LoRA, and measuring whether
LoRA holds that style more reliably than a system prompt, without degrading the
model's general quality.

**Status:** data pipeline, LoRA training and evaluation are done. Results, with 95% confidence
intervals and figures, are in [results/eval/report.md](results/eval/report.md).

## Research question

Can LoRA make a small local LLM consistently exhibit a target conversational style
more reliably than system prompting, especially over long multi-turn conversations,
without degrading helpfulness, instruction following, or safety?

Three systems are compared:

1. **Base**: Qwen3-4B-Instruct-2507 as released.
2. **Prompt-only**: the same model with the persona as a system prompt ([persona/system_prompt.md](persona/system_prompt.md)).
3. **LoRA**: the model fine-tuned on persona conversations, with no system prompt.

The training data is produced by context distillation: a larger teacher model answers
real user prompts while seeing the persona prompt, and the student is trained on those
conversations without it.

## Repository layout

```
persona/system_prompt.md   the persona specification (system prompt, teacher instructions, judge reference)
prompts/                   prompts for the topic tagger, simulated user, and judge
configs/pipeline.yaml      models, sizes and thresholds for the data pipeline
configs/smoke_local.yaml   runs the whole pipeline on a local Ollama model, no API key needed
src/persona_lora/          shared code: config, async LLM client, judge, text checks
scripts/01..06_*.py        the data pipeline, one step per script
scripts/07..11_*.py        the evaluation, one step per script
train/                     LoRA training (train_lora.py) and the vLLM server for evaluation (serve_eval.sh)
scripts/pilot_teachers.py  compare candidate teacher models before generating data
scripts/chat.py            chat with the base, prompt-only, or fine-tuned model locally
results/                   pilot reports and evaluation results kept in git (data/ is not)
```

## Data pipeline

| Step | Script | What it does |
|---|---|---|
| 01 | `01_collect_prompts.py` | First user turns from [oasst2](https://huggingface.co/datasets/OpenAssistant/oasst2) (Apache-2.0), English and Chinese, quality-filtered and deduplicated |
| 02 | `02_tag_prompts.py` | LLM labels: topic, sensitive (distress or emergency), suitable for a short chat reply |
| 02b | `02b_sensitive_prompts.py` | LLM-written prompts in nine sensitive categories (grief, emergencies, self-harm, ...), because oasst2 has only ~40 usable ones |
| 03 | `03_split.py` | Train pools and test sets; two whole topics are held out of training; synthetic sensitive prompts go to training and to `test_sensitive` |
| 04 | `04_generate.py` | Teacher writes persona replies; a simulated user extends multi-turn conversations |
| 05 | `05_judge.py` | LLM judge grades every assistant turn; failing conversations are dropped |
| 05b | `05b_retry_rejected.py` | Rejection sampling: rejected prompts get up to two fresh attempts from the same teacher, each judged again |
| 06 | `06_build_sft.py` | Final chat-format SFT files with no system prompt, one conversation per prompt |

## Evaluation

Base, prompt-only and LoRA answer the same test sets, served by one vLLM server
(`train/serve_eval.sh`) with identical sampling. Metric definitions and the IFEval
conflict split were fixed before any results were seen; see `docs/design.md`, section 6.

| Step | Script | What it does |
|---|---|---|
| 07 | `07_build_pivot_set.py` | Scripted three-turn conversations that turn sensitive at turn 3 (RQ2) |
| 08 | `08_eval_generate.py` | Every system answers every test set: seen/unseen topics, sensitive prompts, pivot, 8-turn drift, IFEval |
| 09 | `09_eval_judge.py` | Two judges from different model families grade every assistant turn, blind to the system |
| 10 | `10_eval_ifeval.py` | IFEval scored with Google's checkers, split by conflict with the persona's length and format |
| 11 | `11_eval_report.py` | Tables with paired bootstrap 95% CIs, agreement between the judges, figures; writes `results/eval/` |

```bash
# Debug on the local Ollama model (no GPU, no API key)
for s in 07_build_pivot_set 08_eval_generate 09_eval_judge 10_eval_ifeval; do
  uv run python scripts/$s.py --config configs/eval_local.yaml $( [ $s = 08_eval_generate ] && echo --limit 2 )
done
uv run python scripts/11_eval_report.py --config configs/eval_local.yaml --out data/eval_local/report

# Real run: start train/serve_eval.sh on a GPU, put VLLM_BASE_URL and VLLM_API_KEY in .env, then
uv run python scripts/07_build_pivot_set.py
uv run python scripts/08_eval_generate.py
uv run python scripts/09_eval_judge.py
uv run python scripts/10_eval_ifeval.py
uv run python scripts/11_eval_report.py

# Optional: Chinese prompts (does the style carry over to another language?)
uv run python scripts/08_eval_generate.py --sets zh
uv run python scripts/09_eval_judge.py --sets zh
```

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and, for the local smoke test, [Ollama](https://ollama.com) with `qwen3:4b-instruct`.

```bash
uv sync
uv run pytest

# Smoke test of the whole pipeline on a local model (no API key)
for s in 01_collect_prompts 02_tag_prompts 02b_sensitive_prompts 03_split 04_generate 05_judge 05b_retry_rejected 06_build_sft; do
  uv run python scripts/$s.py --config configs/smoke_local.yaml $( [ $s = 02_tag_prompts ] && echo --limit 80 )
done

# Real run: put OPENROUTER_API_KEY in .env (see .env.example), then
uv run python scripts/01_collect_prompts.py
uv run python scripts/02_tag_prompts.py
uv run python scripts/02b_sensitive_prompts.py
uv run python scripts/03_split.py
uv run python scripts/pilot_teachers.py --judge   # read data/gen/pilot/teachers.md, choose a teacher
uv run python scripts/04_generate.py
uv run python scripts/05_judge.py
uv run python scripts/05b_retry_rejected.py
uv run python scripts/06_build_sft.py

# Try the prompt-only persona locally
uv run python scripts/chat.py --persona
```

## License

Code: to be decided. Prompts come from oasst2 (Apache-2.0); the base model
Qwen3-4B-Instruct-2507 is Apache-2.0.

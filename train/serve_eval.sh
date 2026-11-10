#!/usr/bin/env bash
# Serve the models for the evaluation (scripts/08_eval_generate.py) on a RunPod GPU.
# One vLLM server in bf16 answers for all three systems:
#   model "qwen3-4b"  the released Qwen3-4B-Instruct-2507 (systems "base" and "prompt")
#   model "persona"   the same model with the LoRA adapter applied (system "lora")
#
# Usage, in /workspace after unzipping persona-adapter.zip there:
#   bash serve_eval.sh <api-key>
# The key can be any long random string; put the same one in your local .env as VLLM_API_KEY.
set -euo pipefail
KEY="${1:?usage: bash serve_eval.sh <api-key>}"

# The RunPod image has a Debian-installed PyJWT that pip cannot uninstall when vllm upgrades it.
pip install -q --ignore-installed PyJWT
pip install -q vllm==0.32.0
exec vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --served-model-name qwen3-4b \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --enable-lora --lora-modules persona=/workspace/adapter --max-lora-rank 16 \
  --host 0.0.0.0 --port 8000 \
  --api-key "$KEY"

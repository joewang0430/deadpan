"""Async client for any OpenAI-compatible endpoint (OpenRouter, Ollama, vLLM),
plus a resumable batch runner that appends results to JSONL as they finish."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Awaitable, Callable

from openai import AsyncOpenAI
from tqdm import tqdm

from .io_utils import read_jsonl
from .text_utils import strip_think


class LLM:
    """One model behind one endpoint, with a concurrency limit and token accounting."""

    def __init__(self, cfg: dict, role: str | None = None, spec: dict | None = None):
        if spec is None:
            if role not in cfg["roles"]:
                raise KeyError(f"Unknown role '{role}'. Defined roles: {list(cfg['roles'])}")
            spec = cfg["roles"][role]
        endpoint_name = spec.get("endpoint", "openrouter")
        endpoint = cfg["endpoints"][endpoint_name]
        api_key = endpoint.get("api_key") or os.environ.get(endpoint.get("api_key_env", ""))
        if not api_key:
            sys.exit(
                f"Missing API key for endpoint '{endpoint_name}'. "
                f"Set {endpoint.get('api_key_env')} in .env (see .env.example)."
            )
        self.name = role or spec["model"]
        self.model = spec["model"]
        self.endpoint = endpoint_name
        # A rented GPU gets a new URL every time, so it can come from .env instead of the config.
        base_url = endpoint.get("base_url") or os.environ.get(endpoint.get("base_url_env", ""))
        if not base_url:
            sys.exit(f"Missing base URL for endpoint '{endpoint_name}'. Set {endpoint.get('base_url_env')} in .env.")
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=spec.get("max_retries", 5),
            timeout=spec.get("timeout", 180),
        )
        self.params = {
            k: spec[k] for k in ("temperature", "max_tokens", "top_p") if k in spec
        }
        self.extra_body = spec.get("extra_body")
        self.sem = asyncio.Semaphore(spec.get("concurrency", 4))
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.truncated = 0

    async def chat(self, messages: list[dict], require_complete: bool = False, **overrides) -> str:
        """Return the reply text. With require_complete, a reply cut off at max_tokens raises
        TruncatedReply instead of being returned half-finished."""
        text, truncated = await self.complete(messages, **overrides)
        if truncated and require_complete:
            raise TruncatedReply(f"reply cut off at max_tokens={self.params.get('max_tokens')}")
        return text

    async def complete(self, messages: list[dict], **overrides) -> tuple[str, bool]:
        """Return the reply text and whether it was cut off at max_tokens."""
        params = {**self.params, **overrides}
        async with self.sem:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                extra_body=self.extra_body,
                **params,
            )
        self.calls += 1
        if resp.usage:
            self.prompt_tokens += resp.usage.prompt_tokens or 0
            self.completion_tokens += resp.usage.completion_tokens or 0
        content = resp.choices[0].message.content if resp.choices else None
        truncated = bool(resp.choices) and resp.choices[0].finish_reason == "length"
        self.truncated += truncated
        return strip_think(content or ""), truncated

    def usage_line(self) -> str:
        return (
            f"[{self.name}] model={self.model} calls={self.calls} "
            f"prompt_tokens={self.prompt_tokens} completion_tokens={self.completion_tokens}"
            + (f" truncated={self.truncated}" if self.truncated else "")
        )


class TruncatedReply(ValueError):
    pass


async def run_resumable(
    items: list[dict],
    worker: Callable[[dict], Awaitable[dict | None]],
    out_path: str | Path,
    desc: str = "",
    id_key: str = "id",
) -> tuple[int, int]:
    """Run `worker` over items concurrently and append each result to out_path.

    Items whose id is already in out_path are skipped, so an interrupted run
    can simply be restarted. Failures are logged and left for the next run.
    Returns (n_done_now, n_failed)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = {row[id_key] for row in read_jsonl(out_path)}
    todo = [it for it in items if it[id_key] not in done]
    if len(done):
        print(f"{desc}: {len(done)} already done, {len(todo)} to go")
    n_ok, n_fail = 0, 0
    pbar = tqdm(total=len(todo), desc=desc)
    with out_path.open("a", encoding="utf-8") as f:

        async def one(item: dict):
            nonlocal n_ok, n_fail
            try:
                result = await worker(item)
            except Exception as e:  # keep going; the item is retried on the next run
                n_fail += 1
                if n_fail <= 5:
                    print(f"\n[{desc}] failed on {item.get(id_key)}: {type(e).__name__}: {e}")
                    if n_fail == 1:
                        traceback.print_exc()
            else:
                if result is not None:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                    f.flush()
                    n_ok += 1
            finally:
                pbar.update(1)

        await asyncio.gather(*(one(it) for it in todo))
    pbar.close()
    if n_fail:
        print(f"{desc}: {n_fail} items failed; rerun the same command to retry them.")
    return n_ok, n_fail

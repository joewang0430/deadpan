"""Chat with a model in the terminal, optionally with the persona system prompt.

  uv run python scripts/chat.py --persona              # prompt-only persona on local Ollama
  uv run python scripts/chat.py                        # plain base model
  uv run python scripts/chat.py --model deadpan   # the fine-tuned model, once it is in Ollama

Commands inside the chat: /reset clears the history, /exit quits."""
from __future__ import annotations

import argparse

from openai import OpenAI

from persona_lora.config import persona_prompt
from persona_lora.io_utils import utf8_console


def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3:4b-instruct")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--persona", action="store_true", help="prepend the persona system prompt")
    ap.add_argument("--temperature", type=float, default=0.7)
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    system = [{"role": "system", "content": persona_prompt()}] if args.persona else []
    history: list[dict] = []
    print(f"model={args.model} persona={'on' if args.persona else 'off'}  (/reset, /exit)")
    while True:
        try:
            user = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user == "/exit":
            break
        if user == "/reset":
            history.clear()
            print("(history cleared)")
            continue
        history.append({"role": "user", "content": user})
        stream = client.chat.completions.create(
            model=args.model, messages=system + history, temperature=args.temperature, stream=True
        )
        print("\nbot> ", end="", flush=True)
        reply = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                reply += delta
                print(delta, end="", flush=True)
        print()
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()

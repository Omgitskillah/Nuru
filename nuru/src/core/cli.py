"""Terminal CLI that sends whatever text you type to a local Ollama model."""

import os

from dotenv import load_dotenv

from core.providers.ollama import chat, ensure_model_loaded, ensure_server_running

load_dotenv()

MODEL = os.environ.get("MODEL", "qwen3:8b")
THINK = os.environ.get("THINK", "true").lower() != "false"
print(f"think={THINK}")


def main() -> None:
    while True:
        try:
            text = input("> ")
        except EOFError:
            break

        ensure_server_running()
        ensure_model_loaded(MODEL)

        first_chunk = True
        for out in chat(MODEL, [{"role": "user", "content": text}], think=THINK):
            if isinstance(out, dict):
                continue
            if first_chunk:
                print("[nuru] ", end="", flush=True)
                first_chunk = False
            print(out, end="", flush=True)
        print()


if __name__ == "__main__":
    main()

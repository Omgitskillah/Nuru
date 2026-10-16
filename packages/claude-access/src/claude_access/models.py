"""The Claude models Nuru can run as its main model (NURU_MAIN_MODEL=anthropic:<id>).

Both think adaptively, steered by the effort parameter, and both reject temperature, top_p and top_k.
context_window and max_output from Anthropic's models overview (checked 2026-10-16); Nuru still caps the
window it uses at NURU_MAX_NUM_CTX. thinking_off is how each turns thinking off: Sonnet 5.5 rejects
{"type": "disabled"} with a 400 and asks for {"type": "between_tools"} (seen in the 2026-10-16 run).
"""

MAIN_MODELS = {
    "claude-haiku-5-5": {"context_window": 1_000_000, "max_output": 128_000,
                         "thinking_off": {"type": "disabled"}},
    "claude-sonnet-5-5": {"context_window": 1_000_000, "max_output": 128_000,
                          "thinking_off": {"type": "between_tools"}},
}

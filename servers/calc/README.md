# calc-mcp

Four arithmetic tools for a small local model, which otherwise does sums in its head and gets
them wrong as plausibly as it gets them right.

| Tool | Does |
|---|---|
| `add(numbers)` | Sum of a list |
| `subtract(a, b)` | `a - b` |
| `multiply(numbers)` | Product of a list |
| `divide(a, b)` | `a / b`; `b = 0` is a `ToolError` |

`add` and `multiply` take a list, so a column of figures is one call. Results are rounded to 10
decimal places, which removes binary float noise (`0.1 + 0.2` returns `0.3`) without losing any
precision a note would use. Each tool's description is a single line: every tool schema is sent
with every request to the model, and the local model's context window is small.

Access is logged under `.logs/` like the other servers (see the [servers README](../README.md));
`CALC_MCP_LOG_DIR` overrides the location.

```bash
cd servers/calc
uv run calc-mcp      # stdio server
uv run pytest
```

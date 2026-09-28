"""calc: an MCP server with four arithmetic tools.

A small local model gets sums wrong when it does them in its head - residuals, averages,
battery-life divisions - and a wrong number looks as plausible as a right one. These give it
exact arithmetic. Each tool's description is one line on purpose: every tool's schema is sent
with every model request, and the local model's context window is small.

Run standalone via stdio with: uv run calc-mcp
"""

import functools
import os
from pathlib import Path
from typing import Callable, TypeVar

from access_logging import AccessLogMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

LOG_DIR = Path(os.environ.get("CALC_MCP_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))

mcp = MCPServer("calc-mcp", middleware=[AccessLogMiddleware(LOG_DIR)])

_T = TypeVar("_T")

# Floats carry binary noise (0.1 + 0.2 = 0.30000000000000004). Rounded to this many decimal
# places, a result reads the way a person would write it, and no figure in a note needs more.
_DECIMALS = 10


def _tool(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Bad input (an empty list, division by zero) reaches the caller as a ToolError with its
    message - see obsidian-mcp's server.py for why only ToolError keeps it."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> _T:
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(f"ValueError: {e}") from e

    return wrapper


def _clean(value: float) -> float:
    return round(value, _DECIMALS)


def _need_numbers(numbers: list[float]) -> None:
    if not numbers:
        raise ValueError("numbers is empty - give at least one number.")


@mcp.tool(description="Sum a list of numbers. Returns the total.")
@_tool
def add(numbers: list[float]) -> float:
    _need_numbers(numbers)
    return _clean(sum(numbers))


@mcp.tool(description="a minus b.")
@_tool
def subtract(a: float, b: float) -> float:
    return _clean(a - b)


@mcp.tool(description="Multiply a list of numbers together. Returns the product.")
@_tool
def multiply(numbers: list[float]) -> float:
    _need_numbers(numbers)
    product = 1.0
    for number in numbers:
        product *= number
    return _clean(product)


@mcp.tool(description="a divided by b.")
@_tool
def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("b is 0 - division by zero is undefined.")
    return _clean(a / b)


def main() -> None:
    mcp.run()

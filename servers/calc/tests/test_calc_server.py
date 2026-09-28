import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from calc_mcp import server


def test_add_sums_a_list():
    assert server.add([189, 45.95]) == 234.95


def test_add_hides_float_noise():
    assert server.add([0.1, 0.2]) == 0.3


def test_subtract():
    assert server.subtract(7951, 7588) == 363


def test_multiply_a_list():
    assert server.multiply([5.35, 24]) == 128.4


def test_divide():
    assert server.divide(30400, 5.35) == pytest.approx(5682.2429906542)


def test_divide_by_zero_is_a_tool_error():
    with pytest.raises(ToolError, match="division by zero"):
        server.divide(1, 0)


@pytest.mark.parametrize("tool", [server.add, server.multiply])
def test_an_empty_list_is_a_tool_error(tool):
    with pytest.raises(ToolError, match="numbers is empty"):
        tool([])


def test_the_four_tools_are_registered_with_one_line_descriptions():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    assert set(tools) == {"add", "subtract", "multiply", "divide"}
    for tool in tools.values():
        assert "\n" not in tool.description and len(tool.description) <= 70


def test_a_call_through_the_protocol_returns_the_number():
    result = asyncio.run(server.mcp.call_tool("multiply", {"numbers": [5.35, 24]}))

    assert "128.4" in str(result)

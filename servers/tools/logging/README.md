# access-logging

Generic MCP `ServerMiddleware` that logs every inbound request/notification
(tool calls, resource reads, `initialize`, etc.), independent of which client
connects (Inspector, Claude Desktop, or anything else) - the logging lives in
the server process itself rather than in any one client.

Each session (starting at `initialize`) gets its own log file, named:

```
<count_up_id>_<date_time>_<client_name>.log
```

`client_name` comes from the client's self-reported `clientInfo.name` at
`initialize`, or `unknown` if none was given. `count_up_id` is the next
integer after the highest id already present in the log directory.

Each log line is a timestamp followed by a dict:

```
2026-09-13 15:42:55,448 -> {'lt': 'initialize', 'params': {...}}
2026-09-13 15:42:55,448 <- {'lt': 'initialize', 'result': 'ok'}
```

## Usage

```python
from pathlib import Path

from access_logging import AccessLogMiddleware
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("my-server", middleware=[AccessLogMiddleware(Path("logs"))])
```

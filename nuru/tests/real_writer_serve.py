"""nuru-serve with a real Session and the real notes server on NuruPluginTestVault; only the model is
scripted. Every message makes the model add "- [ ] buy tickets" to shared/Plan.md, so Session asks first
(a shared write): Yes writes the note for real, No leaves the vault as it was. For WP01's manual byte and
receipt check in real Obsidian (scripted_serve.py never writes).

    uv run --locked python nuru/tests/real_writer_serve.py --dir <scratch> [--port 0]

It makes <scratch>/NuruPluginTestVault, its databases and a client token under <scratch>, then prints one
JSON line: {"port", "token", "vault", "vault_id"}. Stop it with SIGTERM or Ctrl-C.
"""

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core import session  # noqa: E402
from core.cli_logging import CLILogger  # noqa: E402
from core.conversation_store import ConversationStore  # noqa: E402
from core.providers.mcp_tools import MCPTools  # noqa: E402
from core.service.credentials import Credentials  # noqa: E402
from core.service.host import Service  # noqa: E402
from core.service.server import NuruServer  # noqa: E402
from core.service.store import ServiceStore  # noqa: E402
from core.session import Session  # noqa: E402
from core.tool_contract import MODEL_TOOLS  # noqa: E402
from plugin_test_vault import build  # noqa: E402

TASK = "- [ ] buy tickets\n"


def real_session(vault: Path, directory: Path) -> Session:
    """A Session on the real notes server in `vault`; no decision layer, no other servers."""
    params = session.OBSIDIAN_SERVER_PARAMS
    tools = MCPTools([params.model_copy(update={"env": {**params.env, "VAULT_PATH": str(vault), "OBSIDIAN_SEMANTIC_DB": ""}})],
                     model_tools=MODEL_TOOLS)
    tools.connect()
    server_tools = tools.list_tools()
    store = ConversationStore(directory / "conversation.db")
    s = Session(tools, session._hide_injected_args(server_tools), CLILogger(directory / "logs", "scripted", False, "obsidian-mcp"),
                store, store.create_conversation(), server_tools=server_tools)
    s._vault_scope = str(vault)
    return s


def writer(model, messages, *args, **kwargs):
    """The scripted model: the write first; once its result is back, a reply that doesn't claim it."""
    if messages[-1].get("role") != "tool":
        yield {"tool_calls": [{"function": {"name": "write_note", "arguments": {"note": "shared/Plan.md", "content": TASK}}}]}
    else:
        yield "I asked to add the task to [[shared/Plan.md]]; the receipt says what happened."
    yield {"prompt_tokens": 1, "completion_tokens": 1}


def start(directory: Path, port: int = 0):
    """(server, service, token) for a real-writer nuru-serve in `directory`; the caller runs serve_forever."""
    session.ensure_server_running = lambda: None  # no Ollama: the model is scripted
    session.ensure_model_loaded = lambda model, *_: None
    session.chat = writer
    vault = build(directory.resolve())  # absolute: the notes server runs in its own working directory
    credentials = Credentials(directory / "service")
    token = credentials.add("obsidian")
    service = Service(ServiceStore(directory / "service.db"), ConversationStore(directory / "conversation.db"),
                      lambda conversation_id: real_session(vault, directory), vault)
    return NuruServer(service, credentials, port, keepalive_seconds=1), service, token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    args.dir.mkdir(parents=True, exist_ok=True)
    server, service, token = start(args.dir, args.port)
    print(json.dumps({"port": server.server_port, "token": token, "vault": str(service.vault),
                      "vault_id": service.vault_id}), flush=True)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    server.closing = True
    service.close(timeout=5)
    server.server_close()


if __name__ == "__main__":
    main()

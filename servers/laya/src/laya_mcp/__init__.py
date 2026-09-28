def main() -> None:
    # Imported here, not at the top: the config, the log and the question sets are used by Nuru's
    # harness and labelling CLI too, and shouldn't have to build the MCP server to get them.
    from laya_mcp.server import main as run_server

    run_server()


__all__ = ["main"]

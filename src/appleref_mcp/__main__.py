"""Entry point: `appleref-mcp` runs a stdio MCP server."""

from __future__ import annotations

from .server import build_server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()

# appleref-mcp

Offline MCP server for **Apple Developer Documentation**, reading directly from a
Dash-format `Apple_API_Reference.docset`. No network calls, no Dash.app required —
just the `.docset` directory on disk.

## Why

LLM agents working on Apple platforms need accurate, current API documentation,
but `developer.apple.com` is heavy JS, scrapers fight TLS quirks, and Apple's docs
aren't shipped in Xcode anymore. Dash maintains a complete, regularly-updated
offline mirror as a single self-contained docset bundle. This server reads that
bundle directly.

## Requirements

- Python 3.11+
- An `Apple_API_Reference.docset` directory. The easiest way to get one is to
  install [Dash](https://kapeli.com/dash) and download the *Apple API Reference*
  docset there. After download the bundle lives at:
  ```
  ~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset
  ```
  You can copy it anywhere; the server doesn't need Dash running.

## Install

Pick the option that fits your workflow.

### Run straight from GitHub (no install)

```bash
uvx --from git+https://github.com/nzrsky/appleref-mcp appleref-mcp
```

### Install as a uv tool (recommended)

```bash
uv tool install git+https://github.com/nzrsky/appleref-mcp
appleref-mcp           # binary on your PATH
```

To upgrade later: `uv tool upgrade appleref-mcp`.

### From a local checkout

```bash
git clone https://github.com/nzrsky/appleref-mcp.git
cd appleref-mcp
uv tool install --from . appleref-mcp
# or, for hacking:
uv sync && uv run appleref-mcp
```

## Configure docset location

The server looks for the docset in this order:

1. `APPLEREF_DOCSET` environment variable (path to the `.docset` directory, or its parent)
2. `./Apple_API_Reference.docset`
3. `~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset`
4. `~/Apple_API_Reference.docset`

If you don't have Dash installed, copy any existing `Apple_API_Reference.docset`
bundle to one of those paths or point `APPLEREF_DOCSET` at it.

## Wire it up to Claude Code

The fastest path uses `uvx` so nothing needs to be installed up front:

```bash
claude mcp add appleref -- uvx --from git+https://github.com/nzrsky/appleref-mcp appleref-mcp
```

If your docset isn't in the default Dash location, pass `APPLEREF_DOCSET`:

```bash
claude mcp add appleref \
    --env APPLEREF_DOCSET=/path/to/Apple_API_Reference.docset \
    -- uvx --from git+https://github.com/nzrsky/appleref-mcp appleref-mcp
```

After adding, restart Claude Code and verify:

```bash
claude mcp list                    # should show 'appleref'
# or, in a Claude Code session:
/mcp                               # interactive view of connected servers
```

If you installed via `uv tool install`, swap the command for the bare binary:

```bash
claude mcp add appleref -- appleref-mcp
```

### Manual MCP config (other clients)

```json
{
  "mcpServers": {
    "appleref": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/nzrsky/appleref-mcp",
        "appleref-mcp"
      ],
      "env": {
        "APPLEREF_DOCSET": "/path/to/Apple_API_Reference.docset"
      }
    }
  }
}
```

### Quick smoke test

Without a Claude client, you can drive the server directly over stdio:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | appleref-mcp
```

You should see two JSON-RPC responses listing the four tools.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_apple_docs(query, language?, framework?, type?, limit?)` | Find symbols by name. |
| `get_apple_doc(query, language?, framework?)` | Return a full documentation page as markdown. Accepts a name or a canonical path. |
| `list_frameworks(filter?)` | List indexed frameworks. |
| `list_types()` | List symbol kinds with counts. |

`language` is `"swift"` (default) or `"objc"`.

## How it works

The Dash docset stores documentation in two SQLite databases plus a directory
of brotli-compressed files:

```
Contents/Resources/optimizedIndex.dsidx     ← name → URL index (FTS4)
Contents/Resources/Documents/cache.db        ← uuid → (data_id, offset, length)
Contents/Resources/Documents/fs/<data_id>    ← brotli(concatenated DocC RenderJSON)
```

For a given symbol path:

```
canonical = "/documentation/swiftui/griditem"
uuid      = lang_prefix + base64url(sha1(canonical)[:6]).rstrip("=")
            (lang_prefix is "ls" for Swift, "lc" for ObjC)
→ refs WHERE uuid=? → (data_id, offset, length)
→ brotli.decompress(fs/<data_id>)[offset:offset+length] = DocC JSON
```

The server renders the JSON to markdown and returns it.

## Development

```bash
uv sync
uv run pytest -v
```

Tests run end-to-end against a real docset and skip cleanly if none is
available.

## License

MIT

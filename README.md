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

```bash
uv tool install --from . appleref-mcp        # from a checkout
# or
uv pip install -e .
```

## Configure docset location

The server looks for the docset in this order:

1. `APPLEREF_DOCSET` environment variable (path to the `.docset` directory, or its parent)
2. `./Apple_API_Reference.docset`
3. `~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset`
4. `~/Apple_API_Reference.docset`

## Wire it up

### Claude Code

```bash
claude mcp add appleref -- uv tool run appleref-mcp
```

### Manual MCP config

```json
{
  "mcpServers": {
    "appleref": {
      "command": "uv",
      "args": ["tool", "run", "appleref-mcp"],
      "env": {
        "APPLEREF_DOCSET": "/path/to/Apple_API_Reference.docset"
      }
    }
  }
}
```

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

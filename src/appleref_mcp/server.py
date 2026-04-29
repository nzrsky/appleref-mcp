"""MCP server exposing the Apple docset to LLM clients."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .extract import Docset, DocsetNotFound, find_docset
from .render import render_doc

Language = Literal["swift", "objc"]


def build_server() -> FastMCP:
    app = FastMCP(
        "appleref-mcp",
        instructions=(
            "Offline Apple Developer Documentation, served from a local Dash docset. "
            "Tools are stateless — pass `framework` and `language` per-call.\n\n"
            "How to query effectively:\n"
            "• `get_apple_doc` is the primary tool. Use it whenever you have a "
            "  specific symbol in mind — it searches and returns the full page.\n"
            "• If you already know the canonical path "
            "  (e.g. `/documentation/swiftui/griditem`), pass it to `get_apple_doc` "
            "  for a direct fetch — no search.\n"
            "• Inline links in returned markdown are canonical paths; you can pass "
            "  any of them straight back into `get_apple_doc` to navigate.\n"
            "• Pass `framework` (e.g. 'UIKit', 'SwiftUI') to disambiguate common "
            "  names like `View`, `Button`, `present`.\n"
            "• For Apple Objective-C bridged methods (e.g. UIKit), the Swift name "
            "  with parens like `present(_:animated:completion:)` works because "
            "  search also matches the canonical path. The ObjC selector form "
            "  (`presentViewController:animated:completion:`) also works.\n"
            "• `language` defaults to `swift`. Use `objc` to get ObjC declarations "
            "  and selectors in the rendered page.\n"
            "• `list_frameworks` / `list_types` are for exploration — you usually "
            "  do not need them for a focused lookup."
        ),
    )

    # Lazy docset open: gives a clear error from the first tool call rather
    # than crashing before the MCP handshake completes.
    docset_holder: dict[str, Docset] = {}

    def docset() -> Docset:
        if "d" not in docset_holder:
            docset_holder["d"] = Docset(find_docset())
        return docset_holder["d"]

    @app.tool()
    def search_apple_docs(
        query: Annotated[
            str,
            Field(
                description=(
                    "Symbol name, framework name, or keyword. Matches against both "
                    "the index name AND the canonical URL path (case-insensitive), "
                    "so Swift names like `present(_:animated:completion:)` also "
                    "find ObjC-bridged methods. Exact name wins."
                )
            ),
        ],
        language: Annotated[Language, Field(description="API surface language: 'swift' or 'objc'")] = "swift",
        framework: Annotated[
            str | None,
            Field(description="Restrict to a framework, e.g. 'SwiftUI', 'UIKit', 'Foundation'."),
        ] = None,
        type: Annotated[
            str | None,
            Field(
                description=(
                    "Restrict to a kind. Common values: Class, Struct, Method, "
                    "Property, Protocol, Enum, Function, Constant, Framework, Guide."
                )
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Max results")] = 25,
    ) -> str:
        """Search the Apple docset. Returns a list of matching symbols with their kind, framework, and canonical path. Pass any returned path to `get_apple_doc` for full content."""
        try:
            d = docset()
        except DocsetNotFound as e:
            return f"Docset not found.\n\n{e}"
        hits = d.search(query, language=language, framework=framework, type=type, limit=limit)
        if not hits:
            return f"No matches for {query!r} (language={language}, framework={framework}, type={type})."
        out = [f"# Search results for {query!r}", "", f"_{len(hits)} match(es), language={language}_", ""]
        for h in hits:
            fw = h.framework or "?"
            out.append(f"- **{h.name}** _({h.type})_ — `{fw}` — `{h.canonical_path}`")
        out.append("")
        out.append("Call `get_apple_doc` with one of the names or canonical paths above for full content.")
        return "\n".join(out)

    @app.tool()
    def get_apple_doc(
        query: Annotated[
            str,
            Field(
                description=(
                    "Either a symbol name (e.g. 'GridItem', 'UIView', "
                    "'presentViewController:animated:completion:') or a canonical "
                    "path (e.g. '/documentation/swiftui/griditem' or "
                    "'documentation/uikit/uiviewcontroller/present(_:animated:completion:)'). "
                    "Canonical paths are looked up directly without searching, so "
                    "they're the most reliable form when known."
                )
            ),
        ],
        language: Annotated[Language, Field(description="API surface language: 'swift' or 'objc'")] = "swift",
        framework: Annotated[
            str | None,
            Field(
                description=(
                    "Disambiguate when several frameworks define the same name "
                    "(e.g. `View` exists in SwiftUI, AppKit, …)."
                )
            ),
        ] = None,
    ) -> str:
        """Return the full Apple documentation page as markdown — declaration, parameters, discussion, topics, see also.

        Inline cross-references in the returned markdown are canonical paths
        (e.g. `[modalPresentationStyle](/documentation/uikit/uiviewcontroller/modalpresentationstyle)`).
        Pass any of them back into this tool to navigate.
        """
        try:
            d = docset()
        except DocsetNotFound as e:
            return f"Docset not found.\n\n{e}"

        # Direct path?
        q = query.strip()
        if q.startswith("/documentation/") or q.startswith("documentation/"):
            rk = q.lstrip("/")
            doc = d.fetch_by_request_key(rk, language=language)
            if doc is None:
                return f"No content found for path {q!r} (language={language})."
            return render_doc(doc, language=language)

        # Otherwise, search and pick first result that has content.
        result = d.fetch_first_match(q, language=language, framework=framework)
        if result is None:
            return f"No documentation found for {query!r} (language={language}, framework={framework})."
        hit, doc = result
        rendered = render_doc(doc, language=language)
        # Prepend a small banner so the agent knows what was actually opened
        banner = f"_Resolved {query!r} → `{hit.canonical_path}` ({hit.type})_\n\n"
        return banner + rendered

    @app.tool()
    def list_frameworks(
        filter: Annotated[
            str | None, Field(description="Optional case-insensitive substring filter")
        ] = None,
    ) -> str:
        """List all Apple frameworks present in the docset."""
        try:
            d = docset()
        except DocsetNotFound as e:
            return f"Docset not found.\n\n{e}"
        names = d.list_frameworks(filter_text=filter)
        if not names:
            return f"No frameworks match {filter!r}." if filter else "No frameworks indexed."
        out = [f"# Frameworks ({len(names)})", ""] + [f"- {n}" for n in names]
        return "\n".join(out)

    @app.tool()
    def list_types() -> str:
        """List the symbol kinds available in the docset, with counts."""
        try:
            d = docset()
        except DocsetNotFound as e:
            return f"Docset not found.\n\n{e}"
        rows = d.list_types()
        out = ["# Symbol kinds", "", "| Kind | Count |", "| --- | ---: |"]
        for kind, count in rows:
            out.append(f"| {kind} | {count} |")
        return "\n".join(out)

    return app

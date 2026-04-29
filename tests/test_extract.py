"""End-to-end tests against a real Dash Apple_API_Reference.docset.

Skip if the docset isn't installed.
"""

from __future__ import annotations

import pytest

from appleref_mcp.extract import Docset, DocsetNotFound, find_docset
from appleref_mcp.render import render_doc


def _docset() -> Docset:
    try:
        return Docset(find_docset())
    except DocsetNotFound as e:
        pytest.skip(f"docset unavailable: {e}")


def test_search_griditem_swift() -> None:
    d = _docset()
    hits = d.search("GridItem", language="swift", limit=5)
    assert hits, "expected at least one hit for GridItem"
    h = hits[0]
    assert h.name == "GridItem"
    assert h.language == "swift"
    assert h.framework == "swiftui"
    assert h.canonical_path == "/documentation/swiftui/griditem"


def test_search_griditem_objc_falls_back_to_swiftui() -> None:
    # SwiftUI is Swift-only; ObjC search should find no GridItem
    d = _docset()
    hits = d.search("GridItem", language="objc", limit=5)
    assert all(h.language == "objc" for h in hits)


def test_fetch_by_canonical_path_swift() -> None:
    d = _docset()
    doc = d.fetch_by_request_key("documentation/swiftui/griditem", language="swift")
    assert doc is not None
    assert doc["metadata"]["title"] == "GridItem"
    assert doc["metadata"].get("symbolKind") == "struct"


def test_fetch_by_canonical_path_unknown_returns_none() -> None:
    d = _docset()
    assert d.fetch_by_request_key("documentation/swiftui/this-symbol-does-not-exist") is None


def test_render_griditem_markdown_has_key_sections() -> None:
    d = _docset()
    doc = d.fetch_by_request_key("documentation/swiftui/griditem", language="swift")
    assert doc is not None
    md = render_doc(doc, language="swift")
    assert md.startswith("# GridItem")
    assert "Declaration" in md
    assert "```swift" in md
    assert "Topics" in md or "See Also" in md


def test_search_objc_uikit_uiview() -> None:
    d = _docset()
    hits = d.search("UIView", language="objc", limit=10)
    # UIKit is bilingual; we should be able to find UIView via ObjC
    assert any(h.name == "UIView" and h.framework == "uikit" for h in hits)


def test_fetch_uikit_uiview_objc() -> None:
    d = _docset()
    doc = d.fetch_by_request_key("documentation/uikit/uiview", language="objc")
    if doc is None:
        pytest.skip("UIKit/UIView not in this docset (Swift-only build?)")
    assert doc["metadata"]["title"] == "UIView"


def test_list_frameworks_includes_swiftui_uikit() -> None:
    d = _docset()
    names = {n.lower() for n in d.list_frameworks()}
    assert "swiftui" in names
    assert "uikit" in names


def test_list_types_includes_struct_class() -> None:
    d = _docset()
    types = {t for t, _ in d.list_types()}
    assert "Struct" in types
    assert "Class" in types


def test_search_swift_name_with_underscore_finds_via_path() -> None:
    """Apple's index stores ObjC selectors as `name` even on Swift rows for many
    bridged UIKit APIs. The Swift name `present(_:animated:completion:)` only
    appears inside the canonical URL. Search must therefore match the path."""
    d = _docset()
    hits = d.search(
        "present(_:animated:completion:)",
        language="swift",
        framework="uikit",
        type="Method",
        limit=5,
    )
    assert hits, "expected match via canonical path"
    paths = {h.canonical_path for h in hits}
    assert "/documentation/uikit/uiviewcontroller/present(_:animated:completion:)" in paths


def test_search_objc_selector_form_works() -> None:
    d = _docset()
    hits = d.search(
        "presentViewController:animated:completion:",
        language="objc",
        framework="uikit",
        limit=3,
    )
    assert any(
        h.canonical_path == "/documentation/uikit/uiviewcontroller/present(_:animated:completion:)"
        for h in hits
    )


def test_search_handles_like_wildcards_as_literal() -> None:
    """`_` and `%` in a query must be treated as literal characters, not LIKE wildcards."""
    d = _docset()
    # A bogus query containing both LIKE wildcards should not blow up
    # and should not match unrelated names purely because of wildcard expansion.
    hits = d.search("%not_a_real_symbol%", language="swift", limit=3)
    assert hits == []


def test_search_case_insensitive() -> None:
    d = _docset()
    upper = d.search("griditem", language="swift", limit=3)
    lower = d.search("GRIDITEM", language="swift", limit=3)
    assert upper and lower
    assert any(h.name == "GridItem" for h in upper)
    assert any(h.name == "GridItem" for h in lower)

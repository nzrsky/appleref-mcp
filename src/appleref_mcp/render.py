"""Render Apple DocC RenderJSON pages to compact markdown.

We deliberately handle only the subset of inline/block kinds that appear in
Apple's API reference; unknown kinds fall back to their text contents so we
never silently drop information.
"""

from __future__ import annotations

from typing import Any

References = dict[str, dict[str, Any]]


def render_doc(doc: dict[str, Any], *, language: str = "swift") -> str:
    """Render a DocC RenderJSON page as markdown."""
    refs: References = doc.get("references") or {}
    md = doc.get("metadata") or {}
    out: list[str] = []

    title = md.get("title") or "(unknown)"
    role = md.get("role") or md.get("symbolKind") or ""
    out.append(f"# {title}")
    if role:
        out.append(f"_{role}_")

    modules = md.get("modules") or []
    if modules:
        names = ", ".join(m.get("name", "") for m in modules if m.get("name"))
        if names:
            out.append(f"**Module:** {names}")

    platforms = _format_platforms(md.get("platforms") or [])
    if platforms:
        out.append(f"**Platforms:** {platforms}")

    abstract = doc.get("abstract") or []
    if abstract:
        out.append("")
        out.append(_inline(abstract, refs))

    deprecated = doc.get("deprecationSummary") or []
    if deprecated:
        out.append("")
        out.append("> **Deprecated.** " + _blocks(deprecated, refs).strip())

    for section in doc.get("primaryContentSections") or []:
        rendered = _primary_section(section, refs, language=language)
        if rendered:
            out.append("")
            out.append(rendered)

    topics = doc.get("topicSections") or []
    if topics:
        out.append("")
        out.append("## Topics")
        for ts in topics:
            heading = ts.get("title") or ""
            out.append("")
            if heading:
                out.append(f"### {heading}")
            for ident in ts.get("identifiers") or []:
                out.append(_topic_line(ident, refs))

    see_also = doc.get("seeAlsoSections") or []
    if see_also:
        out.append("")
        out.append("## See Also")
        for sa in see_also:
            heading = sa.get("title") or ""
            if heading:
                out.append(f"### {heading}")
            for ident in sa.get("identifiers") or []:
                out.append(_topic_line(ident, refs))

    return "\n".join(line.rstrip() for line in out).rstrip() + "\n"


def _format_platforms(platforms: list[dict[str, Any]]) -> str:
    parts = []
    for p in platforms:
        name = p.get("name")
        if not name:
            continue
        intro = p.get("introducedAt")
        deprec = p.get("deprecatedAt")
        s = name
        if intro:
            s += f" {intro}+"
        if deprec:
            s += f" (deprecated {deprec})"
        if p.get("beta"):
            s += " β"
        parts.append(s)
    return ", ".join(parts)


# ---------------------------------------------------------------- primary content


def _primary_section(section: dict[str, Any], refs: References, *, language: str) -> str:
    kind = section.get("kind")
    if kind == "declarations":
        return _declarations(section, language=language)
    if kind == "parameters":
        return _parameters(section, refs)
    if kind == "content":
        return _blocks(section.get("content") or [], refs)
    if kind == "attributes":
        return _attributes(section, refs)
    if kind == "possibleValues":
        return _possible_values(section, refs)
    if kind == "restBody" or kind == "restEndpoint" or kind == "restParameters":
        # Apple REST/HTTP API reference subset; render content if any
        content = section.get("content") or []
        return _blocks(content, refs)
    # Fallback
    content = section.get("content") or []
    return _blocks(content, refs)


def _declarations(section: dict[str, Any], *, language: str) -> str:
    out: list[str] = []
    for decl in section.get("declarations") or []:
        languages = decl.get("languages") or []
        # Some declarations carry multi-language tokens; pick matching language
        if languages and language not in languages:
            # accept "occ" as alias for objc
            if language == "objc" and "occ" in languages:
                pass
            else:
                continue
        tokens = decl.get("tokens") or []
        text = "".join(t.get("text", "") for t in tokens)
        if text:
            fence_lang = "swift" if language == "swift" else "objc"
            out.append(f"## Declaration\n```{fence_lang}\n{text}\n```")
    return "\n\n".join(out)


def _parameters(section: dict[str, Any], refs: References) -> str:
    items = section.get("parameters") or []
    if not items:
        return ""
    out = ["## Parameters", ""]
    for p in items:
        name = p.get("name") or ""
        content = p.get("content") or []
        body = _blocks(content, refs).strip()
        out.append(f"- **`{name}`** — {body}" if body else f"- **`{name}`**")
    return "\n".join(out)


def _attributes(section: dict[str, Any], refs: References) -> str:
    items = section.get("attributes") or []
    if not items:
        return ""
    out = ["## Attributes", ""]
    for a in items:
        title = a.get("title") or a.get("kind") or "attribute"
        content = a.get("content") or []
        body = _blocks(content, refs).strip()
        out.append(f"- **{title}** — {body}" if body else f"- **{title}**")
    return "\n".join(out)


def _possible_values(section: dict[str, Any], refs: References) -> str:
    items = section.get("values") or []
    if not items:
        return ""
    out = ["## Possible Values", ""]
    for v in items:
        name = v.get("name") or ""
        content = v.get("content") or []
        body = _blocks(content, refs).strip()
        out.append(f"- **`{name}`** — {body}" if body else f"- **`{name}`**")
    return "\n".join(out)


# ---------------------------------------------------------------- block content


def _blocks(blocks: list[dict[str, Any]], refs: References) -> str:
    parts: list[str] = []
    for b in blocks:
        rendered = _block(b, refs)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _block(b: dict[str, Any], refs: References) -> str:
    t = b.get("type")
    if t == "paragraph":
        return _inline(b.get("inlineContent") or [], refs)
    if t == "heading":
        level = max(1, min(6, b.get("level", 2)))
        return f"{'#' * level} {b.get('text', '')}"
    if t == "codeListing":
        lang = b.get("syntax") or ""
        code = "\n".join(b.get("code") or [])
        return f"```{lang}\n{code}\n```"
    if t == "unorderedList":
        return _list(b.get("items") or [], refs, ordered=False)
    if t == "orderedList":
        return _list(b.get("items") or [], refs, ordered=True)
    if t == "aside":
        style = b.get("style") or "note"
        body = _blocks(b.get("content") or [], refs)
        # blockquote with style label
        prefix = f"> **{style.capitalize()}.** "
        return prefix + body.replace("\n", "\n> ")
    if t == "termList":
        return _term_list(b.get("items") or [], refs)
    if t == "table":
        return _table(b, refs)
    # Fallback: try inline / nested content
    if "inlineContent" in b:
        return _inline(b["inlineContent"], refs)
    if "content" in b:
        return _blocks(b["content"], refs)
    return ""


def _list(items: list[dict[str, Any]], refs: References, *, ordered: bool) -> str:
    out: list[str] = []
    for i, item in enumerate(items, 1):
        body = _blocks(item.get("content") or [], refs).strip()
        if not body:
            continue
        marker = f"{i}." if ordered else "-"
        first, *rest = body.splitlines()
        out.append(f"{marker} {first}")
        for line in rest:
            out.append(f"  {line}")
    return "\n".join(out)


def _term_list(items: list[dict[str, Any]], refs: References) -> str:
    out: list[str] = []
    for item in items:
        term = _inline(item.get("term", {}).get("inlineContent") or [], refs)
        definition = _blocks(item.get("definition", {}).get("content") or [], refs).strip()
        if term:
            out.append(f"- **{term}** — {definition}" if definition else f"- **{term}**")
    return "\n".join(out)


def _table(b: dict[str, Any], refs: References) -> str:
    rows = b.get("rows") or []
    header = b.get("header") or "row"
    if not rows:
        return ""
    rendered_rows = [
        [_inline(cell or [], refs) for cell in row] for row in rows
    ]
    if not rendered_rows:
        return ""
    width = max(len(r) for r in rendered_rows)
    rendered_rows = [r + [""] * (width - len(r)) for r in rendered_rows]
    out: list[str] = []
    if header == "row":
        head, *body = rendered_rows
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "|".join(["---"] * width) + "|")
        for r in body:
            out.append("| " + " | ".join(r) + " |")
    else:
        out.append("|" + "|".join([""] * (width + 1)) + "|")
        out.append("|" + "|".join(["---"] * width) + "|")
        for r in rendered_rows:
            out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------- inline content


def _inline(items: list[dict[str, Any]], refs: References) -> str:
    parts: list[str] = []
    for it in items:
        parts.append(_inline_one(it, refs))
    return "".join(parts)


def _inline_one(item: dict[str, Any], refs: References) -> str:
    t = item.get("type")
    if t == "text":
        return item.get("text", "")
    if t == "codeVoice":
        return f"`{item.get('code', '')}`"
    if t == "emphasis":
        return f"*{_inline(item.get('inlineContent') or [], refs)}*"
    if t == "strong":
        return f"**{_inline(item.get('inlineContent') or [], refs)}**"
    if t == "newTerm":
        return f"**{_inline(item.get('inlineContent') or [], refs)}**"
    if t == "reference":
        ident = item.get("identifier", "")
        ref = refs.get(ident) or {}
        title = ref.get("title") or item.get("title") or ident
        url = ref.get("url") or ""
        if url:
            return f"[{title}]({url})"
        return f"`{title}`"
    if t == "link":
        return f"[{item.get('title', item.get('destination', ''))}]({item.get('destination', '')})"
    if t == "image":
        ident = item.get("identifier", "")
        ref = refs.get(ident) or {}
        alt = ref.get("alt") or ""
        url = ref.get("url") or ""
        return f"![{alt}]({url})" if url else ""
    if t == "inlineHead":
        return f"**{_inline(item.get('inlineContent') or [], refs)}**"
    # Fallback: recursively pull text/inlineContent
    if "inlineContent" in item:
        return _inline(item["inlineContent"], refs)
    if "text" in item:
        return item["text"]
    return ""


# ---------------------------------------------------------------- topic lines


def _topic_line(identifier: str, refs: References) -> str:
    ref = refs.get(identifier) or {}
    title = ref.get("title") or identifier.rsplit("/", 1)[-1]
    abstract_md = _inline(ref.get("abstract") or [], refs).strip()
    url = ref.get("url") or ""
    kind = ref.get("kind") or ""
    head = f"- [{title}]({url})" if url else f"- {title}"
    parts = [head]
    if kind:
        parts.append(f"_{kind}_")
    if abstract_md:
        parts.append("— " + abstract_md)
    return " ".join(parts)

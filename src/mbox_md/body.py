"""Pick the message body and turn it into Markdown."""

import codecs
import re
from email.message import EmailMessage, Message
from typing import Literal

BodyKind = Literal["plain", "html"]


def part_text(part: Message) -> str:
    try:
        return part.get_content()  # type: ignore[attr-defined]
    except Exception:
        data = part.get_payload(decode=True) or b""
        cs = part.get_content_charset() or "utf-8"
        try:
            codecs.lookup(cs)
        except LookupError:
            cs = "utf-8"  # e.g. "unknown-8bit"
        return data.decode(cs, "replace")  # type: ignore[union-attr]


# Tags whose contents are never part of the readable message. <head> itself is unwrapped, not dropped:
# when a message never closes it, html.parser nests the whole <body> inside it.
_DROP_TAGS = ("title", "style", "script", "noscript", "template", "meta", "link", "base")


def html_to_md(html: str, max_html: int = 2_000_000) -> str:
    import warnings

    from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning, XMLParsedAsHTMLWarning
    from markdownify import MarkdownConverter

    with warnings.catch_warnings():
        # XHTML mail and one-line bodies that look like URLs would otherwise warn once per message.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(html[:max_html], "html.parser")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for head in soup.find_all("head"):
        head.unwrap()
    unwrap_layout_tables(soup)
    converter = MarkdownConverter(
        heading_style="ATX",
        bullets="*",
        strip=["img"],  # images are signatures, tracking pixels, or attachments listed separately
        escape_underscores=False,
    )
    return tidy_whitespace(converter.convert_soup(soup))


_TABLE_PARTS = ("thead", "tbody", "tfoot", "tr", "td", "th")
_MAX_DATA_CELL = 100


def is_data_table(table) -> bool:
    """Most email tables only position content. Keep a Markdown table only for small, flat grids."""
    if table.get("role") == "presentation" or table.find("table"):
        return False
    if table.find("th"):
        return True
    rows = table.find_all("tr")
    if len(rows) < 2:
        return False
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            return False
        for cell in cells:
            if len(cell.get_text(strip=True)) > _MAX_DATA_CELL or cell.find(["p", "div", "br", "ul", "ol"]):
                return False
    return True


def unwrap_layout_tables(soup) -> None:
    """Turn layout tables into plain blocks so each cell keeps its own paragraphs."""
    layout = [t for t in soup.find_all("table") if not is_data_table(t)]
    for table in layout:
        for part in table.find_all(_TABLE_PARTS):
            if part.find_parent("table") is table:
                part.name = "div"
    for table in layout:
        table.name = "div"


def tidy_whitespace(md: str) -> str:
    """Drop the non-breaking-space padding email layouts use, outside fenced code blocks."""
    out, in_fence = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            line = line.replace(" ", " ")
            if not line.strip():
                line = ""
            else:
                hard_break = line.endswith("  ")  # markdownify's rendering of <br>
                indent = line[: len(line) - len(line.lstrip(" "))]
                line = indent + re.sub(r" {2,}", " ", line[len(indent) :]).rstrip() + ("  " if hard_break else "")
        out.append(line)
    return "\n".join(out)


def extract_body(m: EmailMessage, max_html: int = 2_000_000) -> tuple[str, BodyKind | None, Message | None]:
    """Return (markdown body, kind, the part used). Prefers plain text, falls back to HTML."""
    body_part = m.get_body(preferencelist=("plain", "html"))
    body: str = ""
    kind: BodyKind | None = None
    if body_part is not None:
        text = part_text(body_part)
        if body_part.get_content_subtype() == "html":
            body, kind = html_to_md(text, max_html), "html"
        else:
            body, kind = text, "plain"
        if kind == "plain" and not body.strip():
            alt = m.get_body(preferencelist=("html",))
            if alt is not None:
                body, kind = html_to_md(part_text(alt), max_html), "html"
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, kind, body_part

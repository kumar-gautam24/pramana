"""HTML from the Coverage API into flat, heading-tagged sections.

Pure and I/O-free so it can be tested exhaustively against a recorded fixture rather than
against the network."""

import html
import re
from dataclasses import dataclass

from lxml import etree

#: A heading in this corpus looks like "B.   Nationally Covered Indications" -- a letter or
#: number, a period, whitespace, then the title. CMS marks these with <strong> rather than
#: a heading tag, so structure has to be recovered from the text pattern.
_HEADING = re.compile(r"^\(?([A-Z0-9]{1,3})[.)]\s+\S")


@dataclass(frozen=True)
class Section:
    heading_path: str
    text: str


def unescape_twice(raw: str) -> str:
    """The payload is escaped twice: "&amp;lt;p&amp;gt;" reaches "<p>" only on the second
    pass. Unescaping once leaves markup the parser cannot see, and it silently returns a
    single blob with no headings at all."""
    return html.unescape(html.unescape(raw or ""))


def _clean(text: str) -> str:
    # Non-breaking spaces pad every CMS heading. Left in, a heading path carries invisible
    # characters and two citations to the same section never compare equal.
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _is_heading_position(strong) -> bool:
    """A <strong> is promoted to a heading only when it is the sole or leading content of
    its parent block. Without this, "<strong>A. patient with OSA...</strong>" used as
    inline emphasis inside a sentence would be misread as a section break, splitting a
    single paragraph into a bogus heading plus an orphaned tail."""
    parent = strong.getparent()
    if parent is None:
        return False

    own_text = _clean("".join(strong.itertext()))
    parent_text = _clean("".join(parent.itertext()))
    if own_text == parent_text:
        return True  # the strong is the whole of its block, nothing else to misclassify

    # Otherwise it only counts as a heading if nothing precedes it in the block -- a
    # heading followed by trailing prose (its tail) is still a heading; prose followed by
    # a bolded aside is not.
    return not (parent.text or "").strip() and len(parent) > 0 and parent[0] is strong


def html_to_sections(raw_html: str, root_heading: str) -> list[Section]:
    markup = unescape_twice(raw_html).strip()
    if not markup:
        return []

    root = etree.fromstring(f"<root>{markup}</root>", etree.HTMLParser(recover=True))
    if root is None:
        return []

    heading = root_heading
    buffer: list[str] = []
    sections: list[Section] = []

    def flush() -> None:
        body = _clean(" ".join(buffer))
        if body:
            sections.append(Section(heading_path=heading, text=body))
        buffer.clear()

    def walk(element) -> None:
        nonlocal heading
        if element.tag == "strong":
            candidate = _clean("".join(element.itertext()))
            if _HEADING.match(candidate) and _is_heading_position(element):
                flush()
                heading = f"{root_heading} > {candidate}"
                # The heading's own descendants must not be re-emitted as body text, so
                # the subtree is skipped entirely -- but the tail (text right after the
                # closing </strong>) belongs to the *new* section, not the one just
                # flushed, and must still be captured rather than silently dropped.
                if element.tail:
                    buffer.append(element.tail)
                return
        if element.tag not in ("script", "style") and element.text:
            buffer.append(element.text)
        for child in element:
            walk(child)
        if element.tail:
            buffer.append(element.tail)

    walk(root)
    flush()
    return sections

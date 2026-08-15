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

    for element in root.iter():
        if element.tag == "strong":
            candidate = _clean("".join(element.itertext()))
            if _HEADING.match(candidate):
                flush()
                heading = f"{root_heading} > {candidate}"
                continue
        if element.text and element.tag not in ("script", "style"):
            buffer.append(element.text)
        if element.tail:
            buffer.append(element.tail)

    flush()
    return sections

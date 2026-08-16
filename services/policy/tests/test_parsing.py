import json
import re
from collections import Counter
from pathlib import Path

from lxml import etree

from policy.cms import parse_ncd_response
from policy.parsing import html_to_sections, unescape_twice

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())
CRITERIA_HTML = parse_ncd_response(FIXTURE)[0].sections_html["indications_limitations"]


def _tokens(text: str) -> Counter:
    return Counter(re.findall(r"\w+", text.lower()))


def test_entities_need_two_passes():
    """The API double-escapes: the stored value contains "&amp;lt;p&amp;gt;", which only
    becomes "<p>" after unescaping twice. One pass leaves markup the parser cannot see."""
    assert unescape_twice("&amp;lt;p&amp;gt;") == "<p>"
    assert "<p>" in unescape_twice(CRITERIA_HTML)


def test_solidus_entity_becomes_a_slash():
    """CMS encodes "/" as "&sol;", so closing tags arrive as "&lt;&sol;p&gt;"."""
    assert unescape_twice("&amp;lt;&amp;sol;p&amp;gt;") == "</p>"


def test_finds_the_lettered_headings():
    """Headings in this corpus are <strong> tags carrying a letter prefix, not <h1>-<h6>.
    A generic heading parser finds none of them."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")
    headings = [s.heading_path for s in sections]

    assert any("Nationally Covered Indications" in h for h in headings)
    assert any("Nationally Non-covered Indications" in h for h in headings)


def test_heading_paths_are_rooted():
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert all(s.heading_path.startswith("Indications and Limitations") for s in sections)


def test_no_markup_survives_into_text():
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert not any("<" in s.text or "&lt;" in s.text for s in sections)


def test_the_ahi_criteria_survive_parsing():
    """The numeric criteria are what the whole system reasons over. If parsing drops or
    mangles them, every downstream stage is working from nothing."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")
    body = " ".join(s.text for s in sections)

    assert "greater than or equal to 15 events per hour" in body
    assert "12-week" in body


def test_nonbreaking_space_is_normalised():
    """CMS pads headings with runs of &#160;. Left in place, the heading path contains
    invisible characters and two citations to the same section never compare equal."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert not any("\xa0" in s.heading_path for s in sections)
    assert not any("  " in s.heading_path for s in sections)


def test_content_before_the_first_heading_is_kept():
    """Prose ahead of the first <strong> belongs to the root section. Dropping it loses
    the preamble that scopes everything after it."""
    html = "<p>Intro prose.</p><p><strong>A. First</strong></p><p>Body.</p>"
    sections = html_to_sections(html, root_heading="Root")

    assert sections[0].heading_path == "Root"
    assert "Intro prose." in sections[0].text


def test_empty_html_produces_no_sections():
    assert html_to_sections("", root_heading="Root") == []


def test_no_tokens_are_lost_from_the_real_document():
    """The per-section assertions above only sample a few known phrases. This checks the
    whole document: every word the source markup contains must show up somewhere in the
    parsed output, either as section text or as a heading label. A parser that drops or
    duplicates content can still pass every targeted assertion above while failing this."""
    root_heading = "Indications and Limitations"
    sections = html_to_sections(CRITERIA_HTML, root_heading=root_heading)

    reconstructed = []
    for section in sections:
        label = section.heading_path.rsplit(" > ", 1)[-1]
        if label != root_heading:
            reconstructed.append(label)
        reconstructed.append(section.text)

    markup = unescape_twice(CRITERIA_HTML)
    source = etree.fromstring(f"<root>{markup}</root>", etree.HTMLParser(recover=True))

    # Joined on a space, not on nothing: "".join fuses adjacent text nodes into single
    # tokens ("15events"), and a token the parser dropped can then be masked by a fused
    # neighbour that never existed in either side.
    assert _tokens(" ".join(reconstructed)) == _tokens(" ".join(source.itertext()))


def test_text_after_a_heading_is_kept():
    """Regression for Critical 1: the loop used to `continue` past a recognised heading
    without capturing its .tail, silently deleting whatever prose followed it in the same
    block."""
    sections = html_to_sections(
        "<p><strong>B. Covered Indications</strong> Approve when AHI exceeds 15.</p>",
        root_heading="Root",
    )
    body = " ".join(s.text for s in sections)

    assert "Approve when AHI exceeds 15" in body


def test_bolded_prose_is_not_misread_as_a_heading():
    """Regression for Critical 2: the heading regex alone matches any bolded run shaped
    like a lettered heading, even mid-sentence, which split a single paragraph into a
    bogus heading and an orphaned tail."""
    sections = html_to_sections(
        "<p>Body one. <strong>A. patient with OSA must be treated.</strong> More body.</p>",
        root_heading="Root",
    )

    assert len(sections) == 1
    assert sections[0].heading_path == "Root"
    assert sections[0].text == "Body one. A. patient with OSA must be treated. More body."


def test_heading_text_is_not_duplicated_into_body():
    """Regression for Critical 3: a flat `root.iter()` still visits the descendants of a
    heading it just `continue`d past, so words inside the <strong> were re-emitted as if
    they were body text belonging to the next section."""
    sections = html_to_sections(
        "<p><strong>B. <em>Nationally</em> Covered</strong></p><p>Body text.</p>",
        root_heading="Root",
    )

    assert len(sections) == 1
    assert sections[0].heading_path == "Root > B. Nationally Covered"
    assert sections[0].text == "Body text."

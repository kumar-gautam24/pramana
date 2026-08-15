import json
from pathlib import Path

from policy.cms import parse_ncd_response
from policy.parsing import html_to_sections, unescape_twice

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())
CRITERIA_HTML = parse_ncd_response(FIXTURE)[0].sections_html["indications_limitations"]


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

import pytest

from policy.chunking import chunk_sections
from policy.parsing import Section


def test_a_short_section_becomes_one_chunk():
    sections = [Section(heading_path="Root > A. First", text="Short body.")]

    chunks = chunk_sections(sections)

    assert len(chunks) == 1
    assert chunks[0].text == "Short body."
    assert chunks[0].heading_path == "Root > A. First"


def test_every_chunk_keeps_the_full_heading_path():
    """A citation names the section a reviewer must open. A split that drops the path on
    the second half makes half the corpus uncitable."""
    long_text = " ".join(f"Sentence number {i} about coverage." for i in range(200))
    sections = [Section(heading_path="Root > B. Covered", text=long_text)]

    chunks = chunk_sections(sections, max_chars=400)

    assert len(chunks) > 1
    assert all(c.heading_path == "Root > B. Covered" for c in chunks)


def test_ordinals_are_contiguous_across_sections():
    sections = [
        Section(heading_path="Root > A", text="First body."),
        Section(heading_path="Root > B", text="Second body."),
    ]

    chunks = chunk_sections(sections)

    assert [c.ordinal for c in chunks] == [0, 1]


def test_no_chunk_exceeds_the_limit():
    long_text = " ".join(f"Sentence {i} runs on for a while here." for i in range(300))
    sections = [Section(heading_path="Root > C", text=long_text)]

    chunks = chunk_sections(sections, max_chars=500)

    assert all(len(c.text) <= 500 for c in chunks)


def test_splits_overlap_so_a_criterion_is_not_cut_in_half():
    """A numeric criterion sitting on a split boundary would otherwise appear in neither
    chunk in full, and retrieval would never score it."""
    text = (
        "A. " + ("filler " * 100) + "AHI greater than or equal to 15 events per hour. "
        + ("tail " * 100)
    )
    sections = [Section(heading_path="Root > D", text=text)]

    chunks = chunk_sections(sections, max_chars=400, overlap_chars=200)

    assert any("15 events per hour" in c.text for c in chunks)


def test_empty_input_produces_no_chunks():
    assert chunk_sections([]) == []


def test_whitespace_only_section_is_dropped():
    assert chunk_sections([Section(heading_path="Root", text="   ")]) == []


def test_overlap_must_be_smaller_than_the_window():
    """Overlap at or above the window size never advances the cursor, so the splitter
    would loop forever building identical chunks."""
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections(
            [Section(heading_path="Root", text="x" * 100)], max_chars=100, overlap_chars=100
        )

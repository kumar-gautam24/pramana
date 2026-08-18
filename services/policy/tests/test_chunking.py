import re
from collections import Counter

import pytest

from policy.domain.chunking import chunk_sections
from policy.domain.parsing import Section


def _tokens(text: str) -> Counter:
    return Counter(re.findall(r"\w+", text.lower()))


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


def test_splits_overlap_so_adjacent_chunks_share_text():
    """A numeric criterion sitting on a split boundary would otherwise appear in neither
    chunk in full, and retrieval would never score it. Overlap is what prevents that: the
    tail of one chunk is carried into the next, so adjacent chunks share literal text.
    Sentence-boundary splitting alone (with no overlap) keeps every sentence whole but
    shares nothing across chunks -- this test fails if the carry-forward is removed."""
    sentences = [f"Sentence number {i} about coverage." for i in range(60)]
    sections = [Section(heading_path="Root > D", text=" ".join(sentences))]

    chunks = chunk_sections(sections, max_chars=300, overlap_chars=100)

    assert len(chunks) > 1
    shared_sentences = [
        set(chunks[i].text.split(". ")) & set(chunks[i + 1].text.split(". "))
        for i in range(len(chunks) - 1)
    ]
    assert any(shared_sentences)


def test_empty_input_produces_no_chunks():
    assert chunk_sections([]) == []


def test_whitespace_only_section_is_dropped():
    assert chunk_sections([Section(heading_path="Root", text="   ")]) == []


def test_a_single_sentence_longer_than_the_window_is_cut_rather_than_dropped():
    """The hard-cut branch: no sentence boundary exists to split on, so the splitter cuts
    mid-text. Nothing else reaches that branch, and an unreached branch that silently
    truncated would take a whole criterion out of the corpus with it."""
    sentence = " ".join(f"clause{i} of the coverage requirement" for i in range(60)) + "."
    assert len(sentence) > 2000

    chunks = chunk_sections([Section(heading_path="Root > E", text=sentence)], max_chars=500)

    assert len(chunks) > 1
    assert all(len(c.text) <= 500 for c in chunks)
    # Every word whole in at least one chunk, not merely present across the pile: a cut
    # lands mid-word, and only the overlap carried into the next chunk restores it.
    joined = " | ".join(c.text for c in chunks)
    assert all(word in joined for word in sentence.rstrip(".").split())


@pytest.mark.parametrize("overlap_chars", [0, 150])
def test_no_content_is_lost_across_a_split(overlap_chars):
    """Every targeted test above would still pass if the splitter quietly dropped a
    sentence. This is the one that would not.

    Run without overlap as well as with it: overlap carries the boundary sentences
    forward, so a sentence dropped from the end of one window reappears in the next and
    the loss hides. Zero overlap leaves nowhere for it to hide."""
    sections = [
        Section(
            heading_path="Root > F",
            text=" ".join(f"Sentence number {i} states a coverage rule." for i in range(200)),
        ),
        Section(heading_path="Root > G", text="A short trailing section."),
    ]

    chunks = chunk_sections(sections, max_chars=400, overlap_chars=overlap_chars)

    source = _tokens(" ".join(s.text for s in sections))
    chunked = _tokens(" ".join(c.text for c in chunks))
    # Overlap repeats boundary sentences, so a chunk token count can only exceed the
    # source's, never fall below it. Anything the splitter lost shows up in this
    # subtraction; anything it invented shows up in the set comparison.
    assert source - chunked == Counter()
    assert set(chunked) == set(source)


def test_overlap_must_be_smaller_than_the_window():
    """Overlap at or above the window size never advances the cursor, so the splitter
    would loop forever building identical chunks."""
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections(
            [Section(heading_path="Root", text="x" * 100)], max_chars=100, overlap_chars=100
        )

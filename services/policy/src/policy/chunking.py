"""Sections into retrievable chunks.

Splitting follows the document's own headings rather than a fixed window, so every chunk
keeps the heading path a reviewer would use to find it. A citation that names
"Nationally Covered Indications" can be opened; one that names "chunk 47" cannot."""

import re
from dataclasses import dataclass

from policy.parsing import Section

_SENTENCE_END = re.compile(r"(?<=[.;:])\s+")


@dataclass(frozen=True)
class ChunkRecord:
    ordinal: int
    heading_path: str
    text: str


def _split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    window: list[str] = []
    length = 0
    for sentence in _SENTENCE_END.split(text):
        if length + len(sentence) + 1 > max_chars and window:
            pieces.append(" ".join(window))
            # Carry the tail of the window forward so a criterion sitting on the boundary
            # appears whole in at least one chunk.
            carried: list[str] = []
            carried_len = 0
            for previous in reversed(window):
                if carried_len + len(previous) > overlap_chars:
                    break
                carried.insert(0, previous)
                carried_len += len(previous) + 1
            window = carried
            length = carried_len
        window.append(sentence)
        length += len(sentence) + 1
    if window:
        pieces.append(" ".join(window))

    # A single sentence longer than the window cannot be split on a boundary, so cut it.
    final: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            final.append(piece[:max_chars])
            piece = piece[max_chars - overlap_chars :]
        if piece:
            final.append(piece)
    return final


def chunk_sections(
    sections: list[Section], max_chars: int = 1200, overlap_chars: int = 150
) -> list[ChunkRecord]:
    if overlap_chars >= max_chars:
        # The cursor would never advance, so the splitter would loop forever emitting
        # identical chunks. Fail loudly instead.
        raise ValueError(
            f"overlap_chars must be smaller than max_chars, got {overlap_chars} >= {max_chars}"
        )

    chunks: list[ChunkRecord] = []
    for section in sections:
        body = section.text.strip()
        if not body:
            continue
        for piece in _split(body, max_chars, overlap_chars):
            piece = piece.strip()
            if piece:
                chunks.append(
                    ChunkRecord(
                        ordinal=len(chunks), heading_path=section.heading_path, text=piece
                    )
                )
    return chunks

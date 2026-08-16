"""Fetch, parse, chunk, embed, store.

Idempotent by (document_id, document_version): re-running ingest for a version already
stored is a no-op. Ingest runs on a schedule and after failures, so "run it again" must be
safe rather than a way to double the corpus."""

from dataclasses import dataclass

from policy.chunking import chunk_sections
from policy.cms import SECTION_HEADINGS, NcdRecord
from policy.parsing import html_to_sections
from policy.repositories import chunks as chunks_repo
from policy.repositories import policies as policies_repo


@dataclass(frozen=True)
class IngestResult:
    policies_added: int
    chunks_added: int
    skipped: int


async def ingest_ncd(conn, embedder, records: list[NcdRecord]) -> IngestResult:
    policies_added = chunks_added = skipped = 0

    for record in records:
        existing = await policies_repo.find_by_document_version(
            conn, record.document_id, record.document_version
        )
        if existing is not None:
            skipped += 1
            continue

        sections = []
        for field, raw_html in record.sections_html.items():
            # Indexed, not `.get(field, field)`: a payload field with no heading is a
            # mistake to fix, and defaulting to the raw field name would hide it behind a
            # citation reading "reasons_for_denial".
            sections.extend(html_to_sections(raw_html, root_heading=SECTION_HEADINGS[field]))

        chunk_records = chunk_sections(sections)
        if not chunk_records:
            # A policy with no retrievable text can never be cited, so it is not stored.
            # Storing it anyway would still pass the (document_id, document_version)
            # uniqueness check above on every later run, so the skip would become
            # permanent -- an empty record ingested once would silently and forever
            # shadow any real content CMS later attaches to that same version.
            skipped += 1
            continue

        policy = await policies_repo.insert(
            conn,
            document_id=record.document_id,
            document_version=record.document_version,
            display_id=record.display_id,
            title=record.title,
            effective_from=record.effective_from,
            effective_to=record.effective_to,
            benefit_category=record.benefit_category,
            source_url=record.source_url,
        )
        policies_added += 1

        vectors = embedder.encode([c.text for c in chunk_records])
        rows = [
            (chunk.ordinal, chunk.heading_path, chunk.text, vector)
            for chunk, vector in zip(chunk_records, vectors, strict=True)
        ]
        inserted = await chunks_repo.insert_many(conn, policy.id, rows)
        chunks_added += len(inserted)

    return IngestResult(policies_added, chunks_added, skipped)

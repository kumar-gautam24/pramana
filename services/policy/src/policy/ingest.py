"""Fetch, parse, chunk, embed, store.

Idempotent by (document_id, document_version): re-running ingest for a version already
stored is a no-op. Ingest runs on a schedule and after failures, so "run it again" must be
safe rather than a way to double the corpus."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from policy.chunking import chunk_sections
from policy.cms import NcdRecord
from policy.models import Chunk, Policy
from policy.parsing import html_to_sections

#: Payload field name to the heading a reader would recognise it by.
SECTION_HEADINGS = {
    "item_service_description": "Item/Service Description",
    "indications_limitations": "Indications and Limitations of Coverage",
    "cross_reference": "Cross Reference",
    "reasons_for_denial": "Reasons for Denial",
    "other_text": "Other",
}


@dataclass(frozen=True)
class IngestResult:
    policies_added: int
    chunks_added: int
    skipped: int


async def ingest_ncd(
    session: AsyncSession, embedder, records: list[NcdRecord]
) -> IngestResult:
    policies_added = chunks_added = skipped = 0

    for record in records:
        existing = await session.execute(
            select(Policy.id).where(
                Policy.document_id == record.document_id,
                Policy.document_version == record.document_version,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        sections = []
        for field, raw_html in record.sections_html.items():
            sections.extend(
                html_to_sections(raw_html, root_heading=SECTION_HEADINGS.get(field, field))
            )

        chunks = chunk_sections(sections)
        if not chunks:
            # A policy with no retrievable text can never be cited, so it is not stored.
            # Storing it anyway would still pass the (document_id, document_version)
            # uniqueness check above on every later run, so the skip would become
            # permanent -- an empty record ingested once would silently and forever
            # shadow any real content CMS later attaches to that same version.
            skipped += 1
            continue

        policy = Policy(
            document_id=record.document_id,
            document_version=record.document_version,
            display_id=record.display_id,
            title=record.title,
            effective_from=record.effective_from,
            effective_to=record.effective_to,
            benefit_category=record.benefit_category,
            source_url=record.source_url,
        )
        session.add(policy)
        await session.flush()
        policies_added += 1

        vectors = embedder.encode([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                Chunk(
                    policy_id=policy.id,
                    ordinal=chunk.ordinal,
                    heading_path=chunk.heading_path,
                    text=chunk.text,
                    embedding=vector,
                )
            )
        chunks_added += len(chunks)
        await session.flush()

    return IngestResult(policies_added, chunks_added, skipped)

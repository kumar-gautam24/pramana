"""A retrievable slice of one policy's text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    id: int
    policy_id: int
    ordinal: int
    heading_path: str
    text: str
    #: 384 is bge-small's output width. Changing the embedding model means a migration
    #: and a re-embed of the whole corpus, not a settings edit -- see the column
    #: definition in migrations/0001_policies_and_chunks.sql.
    embedding: list[float]
    #: `tsv` (the generated tsvector column) is deliberately not mapped here: nothing in
    #: Python ever reads it, only the lexical-search SQL does, so pretending it is
    #: application data would be modelling a database implementation detail.

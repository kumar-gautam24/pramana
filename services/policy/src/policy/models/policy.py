"""A coverage determination: one version of one CMS document."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Policy:
    id: int
    document_id: str
    document_version: int
    #: The number a human uses, e.g. "240.4". Distinct from document_id ("226"), which is
    #: the API's internal key -- both are needed, and confusing them silently retrieves
    #: the wrong policy.
    display_id: str
    title: str
    effective_from: date
    #: None means open-ended. The API expresses this as the literal string "N/A".
    effective_to: date | None
    benefit_category: str
    source_url: str
    ingested_at: datetime

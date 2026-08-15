"""The only module that talks to the CMS Coverage API.

Confirmed against the live API on 2026-08-16: GET /v1/data/ncd?ncdid=<id> returns
{"meta": {...}, "data": [ ... ]} and needs neither an API key nor a licence token. NCD
payloads carry no CPT descriptions, so nothing here has to strip them -- but see
docs/decisions/0004 before adding another endpoint."""

from dataclasses import dataclass
from datetime import date, datetime

import httpx

#: Payload fields carrying prose worth retrieving. Ordered as a reader meets them.
SECTION_FIELDS = (
    "item_service_description",
    "indications_limitations",
    "cross_reference",
    "reasons_for_denial",
    "other_text",
)

_VIEW_URL = "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid={id}&ncdver={ver}"


@dataclass(frozen=True)
class NcdRecord:
    document_id: str
    document_version: int
    display_id: str
    title: str
    effective_from: date
    effective_to: date | None
    benefit_category: str
    sections_html: dict[str, str]
    source_url: str


def parse_cms_date(value: str) -> date | None:
    """MM/DD/YYYY, or None for an open-ended policy.

    The API writes "no end date" as the literal string "N/A". Anything else that will not
    parse raises: a misread effective date adjudicates a case against the wrong version of
    policy, which is a worse outcome than refusing to ingest it."""
    text = (value or "").strip()
    if not text or text.upper() == "N/A":
        return None
    return datetime.strptime(text, "%m/%d/%Y").date()


def parse_ncd_response(payload: dict) -> list[NcdRecord]:
    records = []
    for row in payload.get("data", []):
        effective_from = parse_cms_date(row["effective_date"])
        if effective_from is None:
            # A policy with no start date cannot be placed on a timeline, so it cannot be
            # matched to a date of service. Skipping is safer than assuming a date.
            continue
        records.append(
            NcdRecord(
                document_id=str(row["document_id"]),
                document_version=int(row["document_version"]),
                display_id=row["document_display_id"],
                title=row["title"],
                effective_from=effective_from,
                effective_to=parse_cms_date(row.get("effective_end_date", "")),
                benefit_category=row.get("benefit_category", ""),
                sections_html={
                    field: row[field]
                    for field in SECTION_FIELDS
                    if (row.get(field) or "").strip()
                },
                source_url=_VIEW_URL.format(
                    id=row["document_id"], ver=row["document_version"]
                ),
            )
        )
    return records


async def fetch_ncd(client: httpx.AsyncClient, ncd_id: str) -> list[NcdRecord]:
    response = await client.get("/data/ncd", params={"ncdid": ncd_id})
    response.raise_for_status()
    return parse_ncd_response(response.json())

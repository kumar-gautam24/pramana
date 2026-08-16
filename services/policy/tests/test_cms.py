import json
from datetime import date
from pathlib import Path

import pytest

from policy.services.cms import parse_cms_date, parse_ncd_response

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())


def test_parses_the_recorded_ncd():
    records = parse_ncd_response(FIXTURE)

    assert len(records) >= 1
    record = records[0]
    assert record.document_id == "226"
    assert record.display_id == "240.4"
    assert record.effective_from == date(2008, 3, 13)


def test_open_ended_policy_has_no_end_date():
    """The API writes an open-ended policy as the literal string "N/A". Storing that as a
    date would fail; storing it as a far-future date would silently expire the policy."""
    assert parse_cms_date("N/A") is None
    assert parse_cms_date("") is None


def test_parses_a_bounded_end_date():
    assert parse_cms_date("12/31/2019") == date(2019, 12, 31)


@pytest.mark.parametrize("bad", ["2008-03-13", "13/03/2008", "not a date"])
def test_unparseable_date_raises_rather_than_guessing(bad):
    """A misread effective date adjudicates a case against the wrong version of policy,
    which is worse than refusing to ingest."""
    with pytest.raises(ValueError):
        parse_cms_date(bad)


def test_criteria_section_is_captured():
    record = parse_ncd_response(FIXTURE)[0]

    assert "indications_limitations" in record.sections_html
    assert len(record.sections_html["indications_limitations"]) > 1000


def test_empty_sections_are_omitted():
    """NCD 226 has empty other_text and ama_statement. Carrying empty sections into
    chunking produces chunks with no content."""
    record = parse_ncd_response(FIXTURE)[0]

    assert all(value.strip() for value in record.sections_html.values())

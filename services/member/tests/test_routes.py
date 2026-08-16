from datetime import date

import pytest
from fastapi import HTTPException

from member import main
from member.models import Member


async def test_coverage_404s_for_an_unknown_member(routed_session):
    """No record of the member at all -- a data-availability failure that adjudication
    must escalate, not a fact about coverage. Collapsing this into `{"active": false}`
    would let a member missing from the system be treated as one proven uncovered."""
    with pytest.raises(HTTPException) as exc_info:
        await main.coverage("no-such-member", date(2026, 1, 1))

    assert exc_info.value.status_code == 404


async def test_coverage_is_false_not_missing_for_a_known_member_outside_their_window(
    routed_session,
):
    """A member who exists but whose coverage window doesn't cover `on` is a genuine
    fact (false) -- the opposite case from the 404 above, and the two must stay
    distinguishable from outside the service."""
    routed_session.add(
        Member(
            id="m-outside-window",
            birth_date=date(1970, 1, 1),
            sex="F",
            coverage_start=date(2020, 1, 1),
            coverage_end=date(2020, 12, 31),
        )
    )
    await routed_session.flush()

    result = await main.coverage("m-outside-window", date(2026, 1, 1))

    assert result.active is False

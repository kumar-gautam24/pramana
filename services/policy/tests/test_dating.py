from dataclasses import dataclass
from datetime import date

from policy.dating import in_force_on


@dataclass(frozen=True)
class Version:
    name: str
    effective_from: date
    effective_to: date | None
    document_version: int = 1


V1 = Version("v1", date(2008, 3, 13), date(2019, 12, 31))
V2 = Version("v2", date(2020, 1, 1), None)


def test_selects_the_version_covering_the_date():
    assert in_force_on([V1, V2], date(2015, 6, 1)) is V1
    assert in_force_on([V1, V2], date(2026, 6, 1)) is V2


def test_effective_from_is_inclusive():
    assert in_force_on([V1, V2], date(2020, 1, 1)) is V2


def test_effective_to_is_inclusive():
    """CMS states an end date as the last day the version applies, not the first day it
    does not. Treating it as exclusive silently adjudicates that day's claims against the
    following version."""
    assert in_force_on([V1, V2], date(2019, 12, 31)) is V1


def test_a_date_before_any_version_has_no_policy():
    """No policy in force is not the same as the earliest policy. A case dated before the
    determination existed must escalate, not be judged by a rule that did not yet apply."""
    assert in_force_on([V1, V2], date(2001, 1, 1)) is None


def test_open_ended_version_covers_the_future():
    assert in_force_on([V2], date(2099, 1, 1)) is V2


def test_gap_between_versions_yields_nothing():
    a = Version("a", date(2010, 1, 1), date(2010, 6, 30))
    b = Version("b", date(2011, 1, 1), None)

    assert in_force_on([a, b], date(2010, 9, 1)) is None


def test_overlapping_versions_pick_the_latest_start():
    """CMS occasionally publishes overlapping ranges. The later determination is the one
    that governs; picking arbitrarily would make the result depend on row order."""
    old = Version("old", date(2010, 1, 1), None)
    new = Version("new", date(2015, 1, 1), None)

    assert in_force_on([old, new], date(2020, 1, 1)) is new
    assert in_force_on([new, old], date(2020, 1, 1)) is new


def test_no_versions_yields_nothing():
    assert in_force_on([], date(2020, 1, 1)) is None


def test_tied_effective_from_picks_the_higher_document_version():
    """CMS occasionally publishes a corrected or duplicate row with the same start date.
    A higher document_version is the later determination, so it governs -- the same
    principle already applied to effective_from. Without this tiebreaker the result would
    depend on row order, which fails an audit."""
    older = Version("older", date(2015, 1, 1), None, document_version=1)
    newer = Version("newer", date(2015, 1, 1), None, document_version=2)

    assert in_force_on([older, newer], date(2020, 1, 1)) is newer
    assert in_force_on([newer, older], date(2020, 1, 1)) is newer

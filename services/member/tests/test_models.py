from member.models import CpapUsage, Member, SleepStudy


def test_coverage_end_is_nullable():
    """NULL means open-ended coverage. A far-future sentinel date would silently expire
    a member's coverage on that day."""
    assert Member.__table__.c.coverage_end.nullable is True


def test_one_usage_row_per_member_per_night():
    """Adherence is a count of qualifying nights. A duplicate night would inflate that
    count and approve a member who did not meet the threshold."""
    constraints = {c.name for c in CpapUsage.__table__.constraints if c.name}

    assert "uq_cpap_usage_member_night" in constraints


def test_sleep_study_keeps_the_raw_counts_not_only_the_index():
    """NCD 240.4 states criteria in terms of apnea events over recorded hours as well as
    AHI. Storing only the derived index would make one of them unanswerable."""
    columns = SleepStudy.__table__.c

    assert "apnea_events" in columns
    assert "recorded_hours" in columns
    assert "ahi" in columns

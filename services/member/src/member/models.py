"""Synthetic patient records that criterion verifiers query against NCD 240.4."""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Member(Base):
    __tablename__ = "members"

    #: The Synthea patient UUID, reused rather than surrogate-keyed: a regenerated
    #: population maps onto the same rows.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(1), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL means open-ended coverage. A far-future sentinel date would silently expire
    #: a member's coverage on that day.
    coverage_end: Mapped[date | None] = mapped_column(Date, nullable=True)


class Condition(Base):
    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: SNOMED. Criteria match on codes, not prose.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    onset_date: Mapped[date] = mapped_column(Date, nullable=False)


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class SleepStudy(Base):
    __tablename__ = "sleep_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Attended PSG or a Type II/III/IV home study; which one governs which channel
    #: threshold applies.
    test_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Type IV needs at least 3 channels -- the policy's own cutoff.
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Stored alongside the derived index, not instead of it: NCD 240.4 states its
    #: criteria both as a raw event/hour count and as AHI, and only keeping AHI would
    #: make the first form unanswerable.
    apnea_events: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_hours: Mapped[float] = mapped_column(Float, nullable=False)
    ahi: Mapped[float] = mapped_column(Float, nullable=False)


class CpapUsage(Base):
    __tablename__ = "cpap_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    night: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        # Adherence is a count of qualifying nights. A duplicate night would inflate
        # that count and approve a member who did not meet the 70%-of-30-nights
        # threshold.
        UniqueConstraint("member_id", "night", name="uq_cpap_usage_member_night"),
    )


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Nullable: not every note is tied to a specific encounter.
    encounter_id: Mapped[int | None] = mapped_column(
        ForeignKey("encounters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: What the judgment criteria read.
    text: Mapped[str] = mapped_column(Text, nullable=False)

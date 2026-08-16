"""member records

Revision ID: 0001
Revises:
Create Date: 2026-08-16 07:13:54.960625

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "members",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("sex", sa.String(length=1), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "conditions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("onset_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conditions_member_id"), "conditions", ["member_id"], unique=False)
    op.create_table(
        "cpap_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("night", sa.Date(), nullable=False),
        sa.Column("hours", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id", "night", name="uq_cpap_usage_member_night"),
    )
    op.create_index(op.f("ix_cpap_usage_member_id"), "cpap_usage", ["member_id"], unique=False)
    op.create_table(
        "encounters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_encounters_member_id"), "encounters", ["member_id"], unique=False)
    op.create_table(
        "sleep_studies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("test_type", sa.String(length=32), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("apnea_events", sa.Integer(), nullable=False),
        sa.Column("recorded_hours", sa.Float(), nullable=False),
        sa.Column("ahi", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sleep_studies_member_id"), "sleep_studies", ["member_id"], unique=False
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("encounter_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notes_encounter_id"), "notes", ["encounter_id"], unique=False)
    op.create_index(op.f("ix_notes_member_id"), "notes", ["member_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse dependency order: notes references encounters and members; sleep_studies,
    # cpap_usage, encounters, and conditions all reference members.
    op.drop_index(op.f("ix_notes_member_id"), table_name="notes")
    op.drop_index(op.f("ix_notes_encounter_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_index(op.f("ix_sleep_studies_member_id"), table_name="sleep_studies")
    op.drop_table("sleep_studies")
    op.drop_index(op.f("ix_encounters_member_id"), table_name="encounters")
    op.drop_table("encounters")
    op.drop_index(op.f("ix_cpap_usage_member_id"), table_name="cpap_usage")
    op.drop_table("cpap_usage")
    op.drop_index(op.f("ix_conditions_member_id"), table_name="conditions")
    op.drop_table("conditions")
    op.drop_table("members")

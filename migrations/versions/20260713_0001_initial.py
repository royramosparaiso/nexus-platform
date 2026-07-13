"""initial schema — instance + space + area + user_account + audit_log

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("persona_kind", sa.String(32), nullable=False),
        sa.Column("modality", sa.String(32), nullable=False),
        sa.Column("agent_runtime", sa.String(32), nullable=False, server_default="in_process"),
        sa.Column("auth_provider", sa.String(32), nullable=False, server_default="password_totp"),
        sa.Column("console_public_key_pem", sa.Text, nullable=False),
        sa.Column("console_webhook_url", sa.Text, nullable=False),
        sa.Column("platform_private_key_pem", sa.Text, nullable=False),
        sa.Column("platform_public_key_pem", sa.Text, nullable=False),
        sa.Column("manifest_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="bootstrapping"),
        sa.Column("bootstrapped_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "space",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("instance.id"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="internal"),
        sa.Column("is_personal", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance_id", "slug", name="uq_space_slug"),
    )

    op.create_table(
        "area",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("space.id"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("space_id", "slug", name="uq_area_slug"),
    )

    op.create_table(
        "user_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("external_provider", sa.String(32), nullable=True),
        sa.Column("external_subject", sa.String(255), nullable=True),
        sa.Column("is_superadmin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("event_kind", sa.String(64), nullable=False, index=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("user_account")
    op.drop_table("area")
    op.drop_table("space")
    op.drop_table("instance")

"""SQLAlchemy ORM models.

Kept in a single file at v0.6 — split when it grows past ~500 LOC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class InstanceRow(Base):
    """Singleton row — one per Platform process. Written on /_bootstrap."""

    __tablename__ = "instance"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    persona_kind: Mapped[str] = mapped_column(String(32))
    modality: Mapped[str] = mapped_column(String(32))
    agent_runtime: Mapped[str] = mapped_column(String(32), default="in_process")
    auth_provider: Mapped[str] = mapped_column(String(32), default="password_totp")

    console_public_key_pem: Mapped[str] = mapped_column(Text)
    console_webhook_url: Mapped[str] = mapped_column(Text)

    platform_private_key_pem: Mapped[str] = mapped_column(Text)
    platform_public_key_pem: Mapped[str] = mapped_column(Text)

    manifest_json: Mapped[dict] = mapped_column(JSON)  # full InstanceManifest

    status: Mapped[str] = mapped_column(String(32), default="bootstrapping")
    bootstrapped_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class SpaceRow(Base):
    __tablename__ = "space"
    __table_args__ = (UniqueConstraint("instance_id", "slug", name="uq_space_slug"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("instance.id"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32), default="internal")
    is_personal: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    areas: Mapped[list[AreaRow]] = relationship(back_populates="space", cascade="all, delete-orphan")


class AreaRow(Base):
    __tablename__ = "area"
    __table_args__ = (UniqueConstraint("space_id", "slug", name="uq_area_slug"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("space.id"))
    slug: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(100))
    tier: Mapped[str] = mapped_column(String(16))  # core | vertical
    enabled: Mapped[bool] = mapped_column(default=True)
    installed_at: Mapped[datetime] = mapped_column(default=_now)

    space: Mapped[SpaceRow] = relationship(back_populates="areas")


class UserRow(Base):
    __tablename__ = "user_account"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))

    # password_totp provider
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # external providers keep their subject id here
    external_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_superadmin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AuditRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(255))  # user email, agent slug, or "console"
    event_kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)

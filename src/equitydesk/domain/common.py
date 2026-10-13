"""Shared domain primitives: timezone-aware timestamps, ids, base model, errors."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict


class DomainError(ValueError):
    """Base class for domain invariant violations."""


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Naive datetimes are rejected by AwareDatetime; aware values are normalized to UTC.
UtcDateTime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DomainModel(BaseModel):
    """Immutable, strict base for domain entities and value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

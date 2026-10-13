"""Explicit, versioned peer sets. v1 never lets a model choose peers silently."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel, UtcDateTime


class PeerSelectionMethod(StrEnum):
    USER_DEFINED = "user_defined"
    RULE_BASED = "rule_based"


class PeerMember(DomainModel):
    company_id: str = Field(min_length=1)
    security_id: str | None = None
    rationale: str = Field(min_length=1, description="why this company is comparable")


class PeerSet(DomainModel):
    name: str = Field(min_length=1, pattern=r"^[a-z0-9_\-]+$")
    version: int = Field(ge=1)
    target_company_id: str = Field(min_length=1)
    members: tuple[PeerMember, ...] = Field(min_length=1)
    selection_method: PeerSelectionMethod
    created_by: str = Field(min_length=1, description="user id or rule name")
    created_at: UtcDateTime
    # Membership is an explicit analyst choice and may be applied to historical as_of dates
    # (effective_from may precede created_at); peer *values* are always point-in-time.
    effective_from: UtcDateTime = Field(description="first as_of at which this version applies")
    notes: str | None = None

    @property
    def peer_set_id(self) -> str:
        return f"{self.name}:v{self.version}"

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(m.company_id for m in self.members)

    @model_validator(mode="after")
    def _explicit_and_consistent(self) -> PeerSet:
        ids = self.member_ids
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate peer members")
        if self.target_company_id in ids:
            raise ValueError("the target company cannot be its own peer")
        if self.created_by.lower().startswith(("llm", "model", "ai:")):
            raise ValueError("v1 peer sets must be user- or rule-defined, not model-selected")
        return self

    def next_version(
        self,
        members: tuple[PeerMember, ...],
        created_by: str,
        created_at: UtcDateTime,
        notes: str | None = None,
    ) -> PeerSet:
        return PeerSet(
            name=self.name,
            version=self.version + 1,
            target_company_id=self.target_company_id,
            members=members,
            selection_method=self.selection_method,
            created_by=created_by,
            created_at=created_at,
            effective_from=created_at,
            notes=notes,
        )

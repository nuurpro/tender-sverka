from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.auth.schemas import StrictModel, normalize_phone

Role = Literal["admin", "specialist", "viewer"]


class Named(StrictModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Название обязательно")
        return value.strip()


class ProjectCreate(StrictModel):
    mode: Literal["buyer", "supplier"]
    lot_name: str = Field(min_length=1, max_length=500)
    customer: str | None = Field(default=None, max_length=500)
    deadline: AwareDatetime | None = None
    timezone: str = Field(default="Asia/Tashkent", max_length=64)
    report_language: Literal["ru", "uz", "en"] = "ru"

    @field_validator("lot_name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Название обязательно")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неверный часовой пояс") from None
        return value


class ParticipantCreate(Named):
    name: str = Field(min_length=1, max_length=500)
    slot: int = Field(ge=1, le=5, strict=True)


class RequirementInput(StrictModel):
    logical_id: UUID | None = None
    display_id: str = Field(pattern=r"^[TU]-0*[1-9][0-9]*$", max_length=32)
    scope: Literal["product", "participant"]
    item: str | None = Field(default=None, max_length=10000)
    category: str = Field(min_length=1, max_length=128)
    parameter: str | None = Field(default=None, max_length=10000)
    condition: str | None = Field(default=None, max_length=10000)
    importance: Literal["mandatory", "scored", "desirable"]
    predicate: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list, max_length=100)
    origin: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def scope_matches_display_id(self):
        if self.display_id[0] != ("T" if self.scope == "product" else "U"):
            raise ValueError("Идентификатор не соответствует области требования")
        # Nested JSON is flexible for upcoming evidence adapters but bounded.
        from app.workspace.repository import canonical_json
        if len(canonical_json(self.model_dump(mode="json"))) > 100000:
            raise ValueError("Требование слишком большое")
        return self


class ChecklistCreate(StrictModel):
    requirements: list[RequirementInput] = Field(default_factory=list, max_length=1000)

    def values(self):
        return [row.model_dump(mode="json", exclude_none=False, exclude={"logical_id"})
                | ({"logical_id": str(row.logical_id)} if row.logical_id else {}) for row in self.requirements]


class Reason(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value):
        if not value.strip():
            raise ValueError("Причина обязательна")
        return value.strip()


class ChecklistSave(ChecklistCreate):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str | None = Field(default=None, max_length=2000)


class Revision(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class Empty(StrictModel):
    pass


class MembershipCreate(StrictModel):
    phone: str = Field(max_length=32)
    role: Role
    _phone = field_validator("phone", mode="before")(normalize_phone)


class MembershipChange(Reason):
    role: Role

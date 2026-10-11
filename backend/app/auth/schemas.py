import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def normalize_phone(value):
    if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"[+0-9 ()-]+", value, re.ASCII):
        raise ValueError("Неверный формат телефона")
    value = re.sub(r"[ ()-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value, re.ASCII):
        raise ValueError("Неверный формат телефона")
    return value


class PhoneRequest(StrictModel):
    phone: str = Field(max_length=32)
    _phone = field_validator("phone", mode="before")(normalize_phone)


class VerifyRequest(StrictModel):
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)

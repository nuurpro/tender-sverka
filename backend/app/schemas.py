from typing import Literal

from pydantic import BaseModel, ConfigDict

Importance = Literal["mandatory", "scored", "desirable"]
Status = Literal["compliant", "partial", "unconfirmed", "non_compliant", "review"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    block_id: str
    quote: str


class ExtractedRequirement(StrictModel):
    scope: Literal["product", "participant"]
    item: str | None
    parameter: str
    condition: str
    importance: Importance
    citations: list[Citation]


class ChecklistExtraction(StrictModel):
    requirements: list[ExtractedRequirement]


class Requirement(ExtractedRequirement):
    id: str


class ComparisonRow(StrictModel):
    requirement_id: str
    status: Status
    explanation: str
    citations: list[Citation]


class BidComparison(StrictModel):
    rows: list[ComparisonRow]

"""Strict gateway output and frozen execution policy."""
from typing import Annotated, Literal
from decimal import Decimal
from datetime import date
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class Numeric(Strict):
    kind: Literal['numeric']
    operator: Literal['eq','gte','lte','gt','lt','between']
    value: str
    upper: str | None = None
    tolerance: str | None = None
    unit: str

    @field_validator('value','upper','tolerance')
    @classmethod
    def decimal_string(cls,value):
        if value is not None:
            import re
            if not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value) or not Decimal(value).is_finite():
                raise ValueError('Expected a finite decimal string')
        return value

    @model_validator(mode='after')
    def bounds(self):
        if self.operator=='between' and (self.upper is None or Decimal(self.upper)<Decimal(self.value)):
            raise ValueError('Invalid numeric bounds')
        if self.tolerance is not None and Decimal(self.tolerance)<0:
            raise ValueError('Negative tolerance')
        return self


class DatePredicate(Strict):
    kind: Literal['date']
    operator: Literal['before','after','on','by']
    value: str
    basis: Literal['absolute','tender_deadline']
    timezone: str

    @field_validator('value')
    @classmethod
    def valid_date(cls,value):
        date.fromisoformat(value)
        return value

    @field_validator('timezone')
    @classmethod
    def iana_timezone(cls,value):
        try:ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError('Expected an IANA timezone') from None
        return value


class SetPredicate(Strict):
    kind: Literal['set']
    operator: Literal['all','any','none']
    values: list[str] = Field(min_length=1,max_length=200)


class Semantic(Strict):
    kind: Literal['semantic']
    text: str = Field(min_length=1,max_length=4000)


Predicate = Annotated[Numeric | DatePredicate | SetPredicate | Semantic, Field(discriminator='kind')]


class Citation(Strict):
    evidence_id: str = Field(min_length=1,max_length=160)
    quote: str = Field(min_length=1,max_length=10000)


class ExtractedRequirement(Strict):
    scope: Literal['product','participant']
    item: str | None = None
    category: str = Field(min_length=1,max_length=128)
    parameter: str = Field(min_length=1,max_length=2000)
    condition: str = Field(min_length=1,max_length=4000)
    importance: Literal['mandatory','scored','desirable']
    predicate: Predicate
    citations: list[Citation] = Field(min_length=1,max_length=100)


class BidFact(Strict):
    item: str | None = None
    model: str | None = None
    parameter: str = Field(min_length=1,max_length=2000)
    value: str | None = None
    unit: str | None = None
    date: str | None = None
    source_kind: Literal['declaration','certificate','registry','technical_document','unknown']
    citations: list[Citation] = Field(min_length=1,max_length=100)


class Finding(Strict):
    kind: Literal['ambiguity','cross_reference','contradiction','restrictive_language','invalid_evidence','coverage','internal_conflict']
    detail: str = Field(min_length=1,max_length=4000)
    citations: list[Citation] = Field(min_length=1,max_length=100)


class Extraction(Strict):
    requirements: list[ExtractedRequirement] = Field(max_length=2000)
    facts: list[BidFact] = Field(max_length=2000)
    findings: list[Finding] = Field(max_length=2000)


class GatewayProfile(Strict):
    provider: Literal['agentrouter','anthropic'] = 'agentrouter'
    base_url: str = 'https://agentrouter.org/v1'
    model: str = 'deepseek-v4-flash'
    protocol: Literal['chat-json-v1','anthropic-messages-json-v1'] = 'chat-json-v1'
    prompt_version: Literal['grounded-extraction-v1','grounded-extraction-v2'] = 'grounded-extraction-v2'
    schema_version: Literal['grounded-v1'] = 'grounded-v1'
    source_policy: Literal['declaration-is-not-proof-v1'] = 'declaration-is-not-proof-v1'
    context_tokens: int = Field(default=32768,ge=2048,le=1000000)
    output_tokens: int = Field(default=4096,ge=128,le=32768)
    timeout_seconds: float = Field(default=30.,ge=.1,le=120.)
    max_attempts: int = Field(default=2,ge=1,le=3)
    input_price_per_million: str | None = None
    output_price_per_million: str | None = None
    pricing_version: str = 'unverified-no-price-v1'

    @field_validator('base_url')
    @classmethod
    def https_only(cls,value):
        from urllib.parse import urlsplit
        url=urlsplit(value)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Gateway requires a credential-free HTTPS URL')
        return value.rstrip('/')

    @model_validator(mode='after')
    def provider_protocol(self):
        if self.provider=='anthropic' and (self.base_url!='https://api.anthropic.com' or self.protocol!='anthropic-messages-json-v1'):
            raise ValueError('Direct Anthropic requires its official API origin and Messages protocol')
        if self.provider=='agentrouter' and self.protocol!='chat-json-v1':
            raise ValueError('Gateway requires Chat JSON protocol')
        return self

    @field_validator('input_price_per_million','output_price_per_million')
    @classmethod
    def price(cls,value):
        if value is not None and (not Decimal(value).is_finite() or Decimal(value)<0):
            raise ValueError('Invalid pricing')
        return value


class RunBudget(Strict):
    max_requests: int = Field(default=32,ge=1,le=500)
    max_tokens: int = Field(default=1000000,ge=1,le=20000000)
    cost_ceiling: str | None = None

    @field_validator('cost_ceiling')
    @classmethod
    def ceiling(cls,value):
        if value is not None and (not Decimal(value).is_finite() or Decimal(value)<0):
            raise ValueError('Invalid cost ceiling')
        return value


class AnalysisOptions(Strict):
    normalization_job_id: str
    normalization_attempt: int = Field(ge=1,le=20)
    budget: RunBudget = Field(default_factory=RunBudget)

"""Versioned native evidence contract. No application state enters the parser."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, allow_inf_nan=False, validate_default=True)


class ParserLimits(StrictModel):
    max_input_bytes: int = Field(default=50_000_000, ge=1, le=50_000_000)
    max_zip_members: int = Field(default=4000, ge=1, le=4000)
    max_zip_uncompressed_bytes: int = Field(default=100_000_000, ge=1, le=100_000_000)
    max_zip_member_bytes: int = Field(default=20_000_000, ge=1, le=20_000_000)
    max_zip_ratio: int = Field(default=200, ge=1, le=200)
    max_xml_bytes: int = Field(default=8_000_000, ge=1, le=8_000_000)
    max_xml_nodes: int = Field(default=300_000, ge=1, le=300_000)
    max_xml_depth: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=1000, ge=1, le=1000)
    max_blocks: int = Field(default=30_000, ge=1, le=30_000)
    max_cells: int = Field(default=100_000, ge=1, le=100_000)
    max_pixels: int = Field(default=40_000_000, ge=1, le=40_000_000)
    max_text_chars: int = Field(default=1_000_000, ge=1, le=1_000_000)
    max_block_chars: int = Field(default=100_000, ge=1, le=100_000)
    max_output_bytes: int = Field(default=16_000_000, ge=1, le=16_000_000)


class _FrozenVersions(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError('Parser runtime identity is immutable')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny


class RunnerPolicy(StrictModel):
    cpu_seconds: int = Field(default=20, ge=1, le=60)
    memory_bytes: int = Field(default=805306368, ge=67108864, le=1073741824)
    process_limit: int = Field(default=32, ge=4, le=64)
    file_limit: int = Field(default=64, ge=16, le=128)
    file_bytes: int = Field(default=16777216, ge=1024, le=16777216)
    output_bytes: int = Field(default=16777216, ge=1024, le=16777216)
    timeout_seconds: float = Field(default=30.0, ge=.1, le=120)


class ParserProfile(StrictModel):
    version: Literal['native-v1'] = 'native-v1'
    policy_version: Literal['native-policy-v1'] = 'native-policy-v1'
    language: Literal['auto', 'ru', 'uz', 'en'] = 'auto'
    normalize_whitespace: bool = True
    encoding: Literal['auto', 'utf-8', 'utf-16', 'cp1251'] = 'auto'
    csv_delimiter: Literal[',', ';', '\t', '|'] = ','
    code_hash: str = Field(default='0'*64, pattern=r'^[0-9a-f]{64}$')
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    limits: ParserLimits = Field(default_factory=ParserLimits)
    runner_policy: RunnerPolicy = Field(default_factory=RunnerPolicy)

    @field_validator('runtime_versions')
    @classmethod
    def freeze_versions(cls, value):
        allowed = {'pdfplumber', 'python-docx', 'openpyxl', 'pydantic', 'lxml', 'Pillow', 'pdfminer.six', 'pypdfium2', 'python'}
        if not set(value) <= allowed or any(not v or len(v) > 200 for v in value.values()):
            raise ValueError('Invalid parser runtime identity')
        return _FrozenVersions(value)


BBox = tuple[float, float, float, float]


class PDFAnchor(StrictModel):
    kind: Literal['pdf'] = 'pdf'
    page: int = Field(ge=1)
    object_index: int = Field(ge=0)
    original_bbox: BBox
    rendered_bbox: BBox
    media_box: BBox
    crop_box: BBox
    rotation: Literal[0, 90, 180, 270]
    coordinate_space: Literal['pdf_original_user_space'] = 'pdf_original_user_space'


class XMLAnchor(StrictModel):
    kind: Literal['docx'] = 'docx'
    part: str
    element_path: str
    paragraph: int | None = Field(default=None, ge=1)
    table: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    col: int | None = Field(default=None, ge=1)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    merge_origin: str | None = None
    note_id: str | None = None


class CellAnchor(StrictModel):
    kind: Literal['xlsx'] = 'xlsx'
    part: str
    sheet: str
    sheet_id: str
    cell: str
    row: int = Field(ge=1, le=1048576)
    col: int = Field(ge=1, le=16384)
    merge_range: str | None = None
    merge_origin: str | None = None
    raw_formula: str | None = None
    cached_value: str | None = None
    cell_type: str | None = None


class TextAnchor(StrictModel):
    kind: Literal['text', 'csv']
    encoding: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    record: int | None = Field(default=None, ge=1)
    fields: tuple[str, ...] | None = None


class PartAnchor(StrictModel):
    kind: Literal['part'] = 'part'
    part: str
    page: int | None = Field(default=None, ge=1)


Anchor = Annotated[PDFAnchor | XMLAnchor | CellAnchor | TextAnchor | PartAnchor, Field(discriminator='kind')]


class TextMapSegment(StrictModel):
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(ge=0)
    original_start: int = Field(ge=0)
    original_end: int = Field(ge=0)


class DocumentBlock(StrictModel):
    id: str
    kind: Literal['pdf_text', 'paragraph', 'table_cell', 'spreadsheet_cell', 'text_line', 'csv_record']
    original_text: str
    normalized_text: str
    normalization_map: tuple[TextMapSegment, ...]
    original_xml: str | None = None
    anchor: Anchor
    review_flags: tuple[str, ...] = ()


class CoverageEntry(StrictModel):
    id: str
    kind: Literal['native_text', 'page', 'image', 'drawing', 'embedded', 'unknown', 'formula', 'adapter']
    state: Literal['text_extracted', 'extracted', 'ocr_pending', 'vision_pending', 'adapter_unavailable', 'failed', 'corrected', 'review_pending']
    anchor: Anchor
    reason: str | None = None
    block_ids: tuple[str, ...] = ()


class NormalizedDocument(StrictModel):
    schema_version: Literal['1'] = '1'
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    filename: str
    mime_type: str
    format: Literal['pdf', 'docx', 'xlsx', 'txt', 'csv', 'unsupported']
    profile: ParserProfile
    status: Literal['complete', 'partial', 'unavailable']
    blocks: tuple[DocumentBlock, ...]
    coverage: tuple[CoverageEntry, ...]
    physical_pages: int | None = Field(default=None, ge=0)
    analysis_allowed: Literal[False] = False
    warnings: tuple[str, ...] = ()

    @model_validator(mode='after')
    def validate_evidence(self):
        ids = {b.id for b in self.blocks}
        if len(ids) != len(self.blocks) or len({c.id for c in self.coverage}) != len(self.coverage):
            raise ValueError('Duplicate local evidence IDs')
        if any(set(c.block_ids) - ids for c in self.coverage):
            raise ValueError('Coverage references unknown evidence')
        pending = any(c.state not in {'text_extracted', 'extracted', 'corrected'} for c in self.coverage)
        if self.status == 'complete' and pending:
            raise ValueError('Complete document has unresolved coverage')
        for block in self.blocks:
            previous = 0
            for segment in block.normalization_map:
                if not (previous == segment.normalized_start < segment.normalized_end <= len(block.normalized_text)
                        and 0 <= segment.original_start < segment.original_end <= len(block.original_text)):
                    raise ValueError('Invalid normalization source mapping')
                previous = segment.normalized_end
            if previous != len(block.normalized_text):
                raise ValueError('Incomplete normalization source mapping')
        return self

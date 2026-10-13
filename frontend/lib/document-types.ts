export type WorkspaceJob = {
  id: string;
  project_id: string;
  stage: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  attempt: number;
  progress: number;
  error: { code: string; message: string } | null;
  event_sequence: number;
};

export type NormalizationOptions = {
  encoding?: "auto" | "utf-8" | "utf-16" | "cp1251";
  csv_delimiter?: "," | ";" | "\t" | "|";
};

export type NormalizationRun = { job: WorkspaceJob; profile_hash: string; analysis_allowed: false };
type Box = [number, number, number, number];

export type SourceAnchor =
  | { kind: "pdf"; page: number; object_index: number; original_bbox: Box; rendered_bbox: Box; media_box: Box; crop_box: Box; rotation: 0 | 90 | 180 | 270; coordinate_space: "pdf_original_user_space" }
  | { kind: "docx"; part: string; element_path: string; paragraph: number | null; table: number | null; row: number | null; col: number | null; row_span: number; col_span: number; merge_origin: string | null; note_id: string | null }
  | { kind: "xlsx"; part: string; sheet: string; sheet_id: string; cell: string; row: number; col: number; merge_range: string | null; merge_origin: string | null; raw_formula: string | null; cached_value: string | null; cell_type: string | null }
  | { kind: "text" | "csv"; encoding: string; line_start: number; line_end: number; byte_start: number; byte_end: number; char_start: number; char_end: number; record: number | null; fields: string[] | null }
  | { kind: "part"; part: string; page: number | null };

export type DocumentBlock = {
  id: string;
  kind: "pdf_text" | "paragraph" | "table_cell" | "spreadsheet_cell" | "text_line" | "csv_record";
  original_text: string;
  normalized_text: string;
  normalization_map: { normalized_start: number; normalized_end: number; original_start: number; original_end: number }[];
  original_xml: string | null;
  anchor: SourceAnchor;
  review_flags: string[];
};

export type CoverageEntry = {
  id: string;
  kind: "native_text" | "page" | "image" | "drawing" | "embedded" | "unknown" | "formula" | "adapter";
  state: "text_extracted" | "extracted" | "ocr_pending" | "vision_pending" | "adapter_unavailable" | "failed" | "corrected" | "review_pending";
  anchor: SourceAnchor;
  reason: string | null;
  block_ids: string[];
};

export type NormalizedArtifact = {
  id: string;
  document_id: string;
  file_hash: string;
  profile_hash: string;
  attempt: number;
  status: "complete" | "partial" | "unavailable";
  format: "pdf" | "docx" | "xlsx" | "txt" | "csv" | "unsupported";
  block_count: number;
  physical_pages: number | null;
  coverage_summary: Partial<Record<CoverageEntry["state"], number>>;
  created_at: string;
  analysis_allowed: false;
};

export type NormalizationArtifacts = { job: WorkspaceJob; artifacts: NormalizedArtifact[]; analysis_allowed: false };
export type ArtifactDetail = { artifact: NormalizedArtifact; profile: Record<string, unknown>; coverage: CoverageEntry[]; warnings: string[]; analysis_allowed: false };
export type BoundSourceBlock = { artifact_id: string; document_id: string; file_hash: string; block: DocumentBlock; analysis_allowed: false };
export type SourceBlocks = { artifact_id: string; document_id: string; file_hash: string; blocks: DocumentBlock[]; total: number; offset: number; limit: number; coverage: CoverageEntry[]; analysis_allowed: false };

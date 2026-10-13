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
type Affine = [number, number, number, number, number, number];
type Point = [number, number];

export type ParserLimits = {
  max_input_bytes: number;
  max_zip_members: number;
  max_zip_uncompressed_bytes: number;
  max_zip_member_bytes: number;
  max_zip_ratio: number;
  max_xml_bytes: number;
  max_xml_nodes: number;
  max_xml_depth: number;
  max_pages: number;
  max_blocks: number;
  max_cells: number;
  max_pixels: number;
  max_text_chars: number;
  max_block_chars: number;
  max_output_bytes: number;
};

export type OCRLimits = ParserLimits & {
  max_frame_pixels: number;
  max_total_pixels: number;
  max_dimension: number;
  max_raster_pixels: number;
  max_ocr_tsv_bytes: number;
  max_document_seconds: number;
  max_document_cpu_seconds: number;
};

export type RasterTransform = {
  operation: "pdf_render" | "exif_orientation" | "decoder_orientation" | "osd_rotation" | "scale" | "crop" | "deskew";
  forward: Affine;
  inverse: Affine;
  note: string;
};

export type RasterAnchor = {
  kind: "raster";
  frame: number;
  page: number | null;
  source_sha256: string;
  coordinate_space: "image_original_pixels" | "pdf_original_user_space";
  original_size: [number, number];
  raster_size: [number, number];
  original_quad: [Point, Point, Point, Point];
  raster_bbox: Box;
  transforms: RasterTransform[];
  media_box: Box | null;
  crop_box: Box | null;
  pdf_rotation: 0 | 90 | 180 | 270 | null;
  exif_orientation: number;
  decoder_applied_orientation: boolean;
};

export type SourceAnchor =
  | { kind: "pdf"; page: number; object_index: number; original_bbox: Box; rendered_bbox: Box; media_box: Box; crop_box: Box; rotation: 0 | 90 | 180 | 270; coordinate_space: "pdf_original_user_space" }
  | { kind: "docx"; part: string; element_path: string; paragraph: number | null; table: number | null; row: number | null; col: number | null; row_span: number; col_span: number; merge_origin: string | null; note_id: string | null }
  | { kind: "xlsx"; part: string; sheet: string; sheet_id: string; cell: string; row: number; col: number; merge_range: string | null; merge_origin: string | null; raw_formula: string | null; cached_value: string | null; cell_type: string | null }
  | { kind: "text" | "csv"; encoding: string; line_start: number; line_end: number; byte_start: number; byte_end: number; char_start: number; char_end: number; record: number | null; fields: string[] | null }
  | { kind: "part"; part: string; page: number | null }
  | RasterAnchor;

type TextMapSegment = { normalized_start: number; normalized_end: number; original_start: number; original_end: number };

export type NativeDocumentBlock = {
  id: string;
  kind: "pdf_text" | "paragraph" | "table_cell" | "spreadsheet_cell" | "text_line" | "csv_record";
  original_text: string;
  normalized_text: string;
  normalization_map: TextMapSegment[];
  original_xml: string | null;
  anchor: Exclude<SourceAnchor, RasterAnchor>;
  review_flags: string[];
};

/** Transcription offsets refer to recognized characters, never to an embedded text layer. */
export type OCRDocumentBlock = {
  id: string;
  transcription_text: string;
  normalized_text: string;
  normalization_map: TextMapSegment[];
  source_map_space: "ocr_transcription";
  anchor: RasterAnchor;
  confidence: number;
  languages: string[];
  engine: "tesseract";
  child_word_ids: string[];
  review_flags: string[];
} & ({ kind: "ocr_word" } | { kind: "ocr_line" });

export type DocumentBlock = NativeDocumentBlock | OCRDocumentBlock;

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
  format: "pdf" | "docx" | "xlsx" | "txt" | "csv" | "unsupported" | "jpeg" | "png" | "tiff" | "heic" | "bmp" | "webp";
  block_count: number;
  physical_pages: number | null;
  coverage_summary: Partial<Record<CoverageEntry["state"], number>>;
  created_at: string;
  analysis_allowed: false;
};

export type NormalizationArtifacts = { job: WorkspaceJob; artifacts: NormalizedArtifact[]; analysis_allowed: false };
export type ArtifactDetail = { schema_version: "1" | "2"; artifact: NormalizedArtifact; profile: Record<string, unknown>; coverage: CoverageEntry[]; warnings: string[]; analysis_allowed: false };
export type BoundSourceBlock = { schema_version: "1" | "2"; artifact_id: string; document_id: string; file_hash: string; block: DocumentBlock; analysis_allowed: false };
export type SourceBlocks = { schema_version: "1" | "2"; artifact_id: string; document_id: string; file_hash: string; blocks: DocumentBlock[]; total: number; offset: number; limit: number; coverage: CoverageEntry[]; analysis_allowed: false };

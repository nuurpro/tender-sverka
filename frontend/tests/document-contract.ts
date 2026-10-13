import type { BoundSourceBlock, DocumentBlock, RasterAnchor } from "../lib/document-types";

// Compile-time consumer contract: OCR is a transcription, never an embedded quote.
const anchor = {
  kind: "raster", frame: 1, page: null, source_sha256: "a".repeat(64),
  coordinate_space: "image_original_pixels", original_size: [640, 480],
  raster_size: [640, 480], original_quad: [[10, 10], [100, 10], [100, 30], [10, 30]],
  raster_bbox: [10, 10, 100, 30], transforms: [], media_box: null,
  crop_box: null, pdf_rotation: null, exif_orientation: 1,
  decoder_applied_orientation: false,
} satisfies RasterAnchor;

const transcription = {
  id: "b-word", kind: "ocr_word", transcription_text: "16 GB", normalized_text: "16 GB",
  normalization_map: [{ normalized_start: 0, normalized_end: 5, original_start: 0, original_end: 5 }],
  source_map_space: "ocr_transcription", anchor, confidence: 92,
  languages: ["eng"], engine: "tesseract", child_word_ids: [],
  review_flags: ["numeric_ocr_unverified"],
} satisfies DocumentBlock;

const native = {
  id: "b-native", kind: "paragraph", original_text: "  Товар  ", normalized_text: "Товар",
  normalization_map: [{ normalized_start: 0, normalized_end: 5, original_start: 2, original_end: 7 }],
  original_xml: null, anchor: { kind: "part", part: "word/document.xml", page: null }, review_flags: [],
} satisfies DocumentBlock;

function sourceText(block: DocumentBlock): string {
  if (block.kind === "ocr_word" || block.kind === "ocr_line") {
    // @ts-expect-error A consumer must not treat OCR as an original embedded quote.
    const original: string = block.original_text;
    void original;
    return block.transcription_text;
  }
  return block.original_text;
}

const envelope = {
  schema_version: "2",
  artifact_id: "artifact", document_id: "original", file_hash: "a".repeat(64),
  block: transcription, analysis_allowed: false,
} satisfies BoundSourceBlock;
void [sourceText(native), sourceText(envelope.block)];

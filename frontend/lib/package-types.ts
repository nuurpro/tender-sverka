export type PackageKind = "tender" | "bid";
export type FormatSupport = "inspection_available" | "ocr_required" | "adapter_unavailable" | "provenance";

export type PackageDocument = {
  id: string;
  name: string;
  relative_path: string;
  size: number;
  mime_type: string;
  file_hash: string;
  provenance_role: string;
  format_support: FormatSupport;
};

export type PackageSummary = {
  id: string;
  project_id: string;
  kind: PackageKind;
  participant_id: string | null;
  version: number;
  status: string;
  created_at: string;
};

export type PackageDetail = PackageSummary & {
  manifest: Record<string, unknown>;
  documents: PackageDocument[];
};

export type PackagePreflight = {
  inspection_status: "pending" | "inspected";
  analysis_allowed: boolean;
  logical_files: number;
  logical_bytes: number;
  wire_files: number;
  wire_bytes: number;
  expanded_bytes: number;
  formats: { document_id: string; mime_type: string; support: FormatSupport }[];
  page_count: number | null;
  token_count: number | null;
  token_upper_bound: number | null;
  blockers: string[];
  warnings: string[];
};

export type PackageUpload = {
  kind: PackageKind;
  participantId?: string;
  files: ReadonlyArray<{ file: File; relativePath?: string }>;
};

export type PackageUploadResult = {
  package: PackageDetail;
  inspection_job_id: string;
};

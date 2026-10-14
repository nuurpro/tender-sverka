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

export type PackageAccounting = {
  wire_files: number;
  wire_bytes: number;
  logical_files: number;
  logical_bytes: number;
  archive_containers: number;
  expanded_bytes: number;
};

export type ArchiveSource = { path: string; sha256: string };

export type ArchiveIssue = {
  path: string;
  code: "ARCHIVE_CORRUPTED" | "UNSUPPORTED_FORMAT";
  message: string;
};

type ArchiveLimitsV2 = {
  max_files: number;
  max_bytes: number;
  max_entries: 2048;
  max_depth: 3;
  max_seconds: 5;
};

type ArchiveAdmissionIdentity = {
  code_hash: string;
  runtime_versions: Record<string, string>;
  archive_toolchain: Record<string, string>;
  runner_policy: {
    cpu_seconds: number;
    memory_bytes: number;
    process_limit: number;
    file_limit: number;
    file_bytes: number;
    output_bytes: 100_262_148;
    timeout_seconds: number;
  };
};

export type ArchiveAdmissionV2 = ArchiveAdmissionIdentity & {
  policy_version: "archive-admission-v2";
  limits: ArchiveLimitsV2;
};

export type ArchiveAdmissionV3 = ArchiveAdmissionIdentity & {
  policy_version: "archive-admission-v3";
  runner_policy: ArchiveAdmissionIdentity["runner_policy"] & {
    file_bytes: 50_000_000;
    tmp_bytes: 16_777_216;
    memfd_bytes: 50_000_000;
    oracle_memory_bytes: 268_435_456;
  };
  limits: ArchiveLimitsV2 & {
    max_metadata_bytes: 8_000_000;
    max_metadata_dictionary_bytes: 67_108_864;
    max_decoder_dictionary_bytes: 67_108_864;
  };
};

export type ArchiveAdmissionV4 = ArchiveAdmissionIdentity & {
  policy_version: "archive-admission-v4";
  runner_policy: ArchiveAdmissionIdentity["runner_policy"] & {
    file_bytes: 50_000_000;
    tmp_bytes: 67_108_864;
  };
  limits: ArchiveLimitsV2;
};

export type ArchiveAdmission = ArchiveAdmissionV2 | ArchiveAdmissionV3 | ArchiveAdmissionV4;

export type LegacyPackageManifest = {
  schema_version: 1;
  accounting: PackageAccounting;
  documents: PackageDocument[];
};

export type ArchivePackageManifest = {
  schema_version: 2;
  accounting: PackageAccounting & { directory_entries: number };
  archive_admission: ArchiveAdmissionV2 | ArchiveAdmissionV3;
  // Outermost archive first; this field belongs to the immutable manifest.
  documents: (PackageDocument & { source_chain: ArchiveSource[] })[];
};

export type StandardArchivePackageManifest = {
  schema_version: 3;
  accounting: PackageAccounting & { directory_entries: number };
  archive_admission: ArchiveAdmissionV4;
  issues: ArchiveIssue[];
  documents: (PackageDocument & { source_chain: ArchiveSource[] })[];
};

export type PackageManifest = LegacyPackageManifest | ArchivePackageManifest | StandardArchivePackageManifest;

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
  manifest: PackageManifest;
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
  issues: ArchiveIssue[];
};

export type PackageUpload = {
  kind: PackageKind;
  participantId?: string;
  files: ReadonlyArray<{ file: File; relativePath?: string }>;
};

export type PackageUploadResult = {
  package: PackageDetail;
  inspection_job_id: string | null;
};

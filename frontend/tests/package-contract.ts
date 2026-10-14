import type { PackageManifest, ArchivePackageManifest, StandardArchivePackageManifest, ArchiveIssue, PackageUploadResult, PackagePreflight } from "../lib/package-types";

// Old immutable manifests stay readable; archive provenance requires version 2.
function sourceChain(manifest: PackageManifest): string[] {
  if (manifest.schema_version === 1) {
    // @ts-expect-error Historical manifests have no isolated admission snapshot.
    void manifest.archive_admission;
    return [];
  }
  const admission = manifest.archive_admission;
  if (admission.policy_version === "archive-admission-v3") {
    const metadataCap: 8_000_000 = admission.limits.max_metadata_bytes;
    const dictionaryCap: 67_108_864 = admission.limits.max_metadata_dictionary_bytes;
    const decoderCap: 67_108_864 = admission.limits.max_decoder_dictionary_bytes;
    const fileCap: 50_000_000 = admission.runner_policy.file_bytes;
    const tmpCap: 16_777_216 = admission.runner_policy.tmp_bytes;
    const memfdCap: 50_000_000 = admission.runner_policy.memfd_bytes;
    const oracleCap: 268_435_456 = admission.runner_policy.oracle_memory_bytes;
    void [metadataCap, dictionaryCap, decoderCap, fileCap, tmpCap, memfdCap, oracleCap];
  } else {
    // @ts-expect-error Earlier frozen admission has no recorded metadata budget.
    void admission.limits.max_metadata_bytes;
    // @ts-expect-error Earlier policy did not record separate oracle/tmp budgets.
    void admission.runner_policy.tmp_bytes;
  }
  return manifest.documents.flatMap(document => document.source_chain.map(source => source.path));
}

const archived = {
  schema_version: 2,
  accounting: {
    wire_files: 1, wire_bytes: 1200, logical_files: 1, logical_bytes: 900,
    archive_containers: 1, expanded_bytes: 900, directory_entries: 1,
  },
  archive_admission: {
    policy_version: "archive-admission-v3", code_hash: "a".repeat(64),
    runtime_versions: { python: "3.14.4" }, archive_toolchain: { libarchive: "libarchive 3.8.5" },
    limits: {
      max_files: 20, max_bytes: 50_000_000, max_entries: 2048, max_depth: 3, max_seconds: 5,
      max_metadata_bytes: 8_000_000, max_metadata_dictionary_bytes: 67_108_864,
      max_decoder_dictionary_bytes: 67_108_864,
    },
    runner_policy: {
      cpu_seconds: 20, memory_bytes: 805_306_368, process_limit: 32,
      file_limit: 64, file_bytes: 50_000_000, output_bytes: 100_262_148, timeout_seconds: 60,
      tmp_bytes: 16_777_216, memfd_bytes: 50_000_000, oracle_memory_bytes: 268_435_456,
    },
  },
  documents: [{
    id: "doc", name: "bid.pdf", relative_path: "bid.zip/bid.pdf", size: 900,
    mime_type: "application/pdf", file_hash: "b".repeat(64), provenance_role: "document",
    format_support: "inspection_available",
    source_chain: [{ path: "bid.zip", sha256: "c".repeat(64) }],
  }],
} satisfies ArchivePackageManifest;

void sourceChain(archived);

const historical = {
  ...archived,
  archive_admission: {
    ...archived.archive_admission, policy_version: "archive-admission-v2",
    limits: { max_files: 20, max_bytes: 50_000_000, max_entries: 2048, max_depth: 3, max_seconds: 5 },
    runner_policy: {
      cpu_seconds: 20, memory_bytes: 805_306_368, process_limit: 32,
      file_limit: 64, file_bytes: 16_777_216, output_bytes: 100_262_148, timeout_seconds: 60,
    },
  },
} satisfies ArchivePackageManifest;
void sourceChain(historical);

const skipped: ArchiveIssue = {
  path: "damaged.zip", code: "ARCHIVE_CORRUPTED", message: "Архив повреждён",
};
const standard = {
  schema_version: 3,
  accounting: {
    wire_files: 1, wire_bytes: 1200, logical_files: 0, logical_bytes: 0,
    archive_containers: 1, expanded_bytes: 0, directory_entries: 0,
  },
  archive_admission: {
    policy_version: "archive-admission-v4", code_hash: "d".repeat(64),
    runtime_versions: { python: "3.14.4" },
    archive_toolchain: { rarfile: "4.5", py7zr: "1.1.4" },
    limits: { max_files: 20, max_bytes: 50_000_000, max_entries: 2048, max_depth: 3, max_seconds: 5 },
    runner_policy: {
      cpu_seconds: 20, memory_bytes: 805_306_368, process_limit: 32,
      file_limit: 64, file_bytes: 50_000_000, output_bytes: 100_262_148,
      timeout_seconds: 30, tmp_bytes: 67_108_864,
    },
  },
  issues: [skipped],
  documents: [{
    id: "bad-original", name: "damaged.zip", relative_path: "damaged.zip", size: 1200,
    mime_type: "application/zip", file_hash: "e".repeat(64), provenance_role: "archive",
    format_support: "provenance", source_chain: [],
  }],
} satisfies StandardArchivePackageManifest;

function skippedFiles(manifest: PackageManifest): ArchiveIssue[] {
  if (manifest.schema_version !== 3) {
    // @ts-expect-error Historical immutable snapshots have no issue records.
    void manifest.issues;
    return [];
  }
  const temporaryCap: 67_108_864 = manifest.archive_admission.runner_policy.tmp_bytes;
  // @ts-expect-error Standard adapters do not claim the old native oracle policy.
  void manifest.archive_admission.runner_policy.oracle_memory_bytes;
  void temporaryCap;
  return manifest.issues;
}

const allBadUpload = {
  package: {
    id: "package", project_id: "project", kind: "tender", participant_id: null,
    version: 1, status: "uploaded_with_issues", created_at: "2026-10-14T00:00:00Z",
    manifest: standard, documents: standard.documents,
  },
  inspection_job_id: null,
} satisfies PackageUploadResult;
const allBadPreflight = {
  inspection_status: "inspected", analysis_allowed: false,
  logical_files: 0, logical_bytes: 0, wire_files: 1, wire_bytes: 1200,
  expanded_bytes: 0, formats: [], page_count: 0, token_count: 0,
  token_upper_bound: 0, blockers: ["no_available_documents"],
  warnings: ["archive_files_skipped"], issues: [skipped],
} satisfies PackagePreflight;
void [skippedFiles(standard), sourceChain(standard), allBadUpload, allBadPreflight];

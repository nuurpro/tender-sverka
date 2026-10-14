import type { SourceAnchor, WorkspaceJob } from "./document-types";

export type AiPolicy = { allowed: boolean; version: number };
export type AiPolicyChange = { allowed: boolean; expected_version: number; reason: string };
export type AnalysisBudget = { max_requests: number; max_tokens: number; cost_ceiling: string | null };
export type AnalysisOptions = { normalization_job_id: string; normalization_attempt: number; budget: AnalysisBudget };
export type EvidenceCitation = {
  evidence_id: string; artifact_id: string; document_id: string; filename: string; block_id: string;
  anchor: SourceAnchor; quote_kind: "native_original" | "ocr_transcription"; confidence: number | null;
  review_flags: string[]; quote: string; source_start: number; source_end: number; validated: true;
};
export type Predicate =
  | { kind: "numeric"; operator: "eq" | "gte" | "lte" | "gt" | "lt" | "between"; value: string; upper: string | null; tolerance: string | null; unit: string }
  | { kind: "date"; operator: "before" | "after" | "on" | "by"; value: string; basis: "absolute" | "tender_deadline"; timezone: string }
  | { kind: "set"; operator: "all" | "any" | "none"; values: string[] }
  | { kind: "semantic"; text: string };
export type GroundedRequirement = {
  scope: "product" | "participant"; item: string | null; category: string; parameter: string; condition: string;
  importance: "mandatory" | "scored" | "desirable"; predicate: Predicate; citations: EvidenceCitation[]; review_flags: string[];
};
export type GroundedFact = {
  item: string | null; model: string | null; parameter: string; value: string | null; unit: string | null; date: string | null;
  source_kind: "declaration" | "certificate" | "registry" | "technical_document" | "unknown";
  citations: EvidenceCitation[]; review_flags: string[];
};
export type AnalysisFinding = {
  kind: "ambiguity" | "cross_reference" | "contradiction" | "restrictive_language" | "invalid_evidence" | "coverage" | "internal_conflict";
  detail: string; citations: EvidenceCitation[]; review_flags?: string[];
};
export type AnalysisCoverage = { state: string; reason?: string | null; filename?: string; detail?: unknown; anchor?: SourceAnchor };
export type AnalysisArtifact = { id: string; document_id: string; filename: string; [key: string]: unknown };
export type AnalysisPreflight = {
  external_ai_allowed: boolean; policy_version: number; frozen_identity: string;
  input_token_upper_bound: number; package_text_token_upper_bound: number; request_count: number;
  extraction_chunk_count: number; reference_lookup_count: number; reference_links: Record<string, unknown>[];
  max_provider_attempts: number; context_tokens: number; output_tokens: number; budget: AnalysisBudget;
  worst_case_run_tokens: number; pricing_known: boolean; coverage_warnings: AnalysisCoverage[]; artifacts: AnalysisArtifact[];
};
export type AnalysisResult = {
  requirements?: GroundedRequirement[]; facts?: GroundedFact[]; findings?: AnalysisFinding[];
  coverage?: AnalysisCoverage[]; source_policy?: string; [key: string]: unknown;
};
export type AnalysisRun = {
  id: string; project_id: string; package_id: string; participant_id: string | null; checklist_id: string | null;
  expected_revision: number; policy_version: number; status: "draft" | "published"; result: AnalysisResult; created_at: string;
};
export type AnalysisStart = { job: WorkspaceJob; run: AnalysisRun };
export type AnalysisDetail = AnalysisStart & { frozen: Record<string, unknown>; calls: Record<string, unknown>[] };

export type RequirementInput = {
  logical_id?: string; display_id: string; scope: "product" | "participant"; item: string | null;
  category: string; parameter: string | null; condition: string | null; importance: "mandatory" | "scored" | "desirable";
  predicate: Predicate | Record<string, unknown>; citations: EvidenceCitation[]; origin: Record<string, unknown>;
};
export type ChecklistRequirement = RequirementInput & {
  id: string; organization_id: string; checklist_id: string; logical_id: string; created_at: string;
};
export type ChecklistSummary = {
  id: string; organization_id: string; project_id: string; version: number; revision: number;
  status: "draft" | "approved"; parent_version_id: string | null; approved_by: string | null;
  approved_at: string | null; created_at: string;
};
export type ChecklistDetail = ChecklistSummary & { requirements: ChecklistRequirement[] };
export type ChecklistSave = { expected_revision: number; reason: string | null; requirements: RequirementInput[] };

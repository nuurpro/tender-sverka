export type Importance = "mandatory" | "scored" | "desirable";
export type Status = "compliant" | "partial" | "unconfirmed" | "non_compliant" | "review";
export type Citation = { block_id: string; quote: string };
export type Block = { id: string; filename: string; location: string; page: number | null; text: string };
export type UploadedFile = { id: string; name: string; size: number };
export type Requirement = {
  id: string; scope: "product" | "participant"; item: string | null;
  parameter: string; condition: string; importance: Importance; citations: Citation[];
};
export type ComparisonRow = { requirement_id: string; status: Status; explanation: string; citations: Citation[] };
export type Score = {
  percent: number | null; min_percent: number; max_percent: number;
  risk: boolean; mandatory_review: boolean; review_count: number;
};
export type Bid = { name: string; files: UploadedFile[]; blocks: Block[] };
export type BidResult = { comparison: { rows: ComparisonRow[] }; score: Score };
export type Check = {
  id: string; status: "idle" | "queued" | "running" | "done" | "failed";
  stage: string; files: UploadedFile[]; blocks: Block[]; requirements: Requirement[];
  bids: Record<string, Bid>; results: Record<string, BidResult>; approved: boolean;
  error: string | null; created_at: string;
};
export type Health = { status: string; user: string; ai_mode: string; model: string; provider?: string; api_style?: string };

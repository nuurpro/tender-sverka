export type Role = "admin" | "specialist" | "viewer";
export type Organization = { id: string; name: string; role: Role };
export type WorkspaceSession = {
  user: { id: string; phone: string | null; display_name: string | null };
  csrf_token: string;
  organizations: Organization[];
};
export type Project = {
  id: string;
  organization_id: string;
  mode: "supplier" | "buyer";
  lot_name: string;
  customer: string | null;
  deadline: string | null;
  timezone: string;
  report_language: "ru" | "uz" | "en";
  revision: number;
  created_at: string;
};
export type NewProject = {
  mode: Project["mode"];
  lot_name: string;
  customer?: string | null;
  deadline?: string | null;
  timezone?: string;
  report_language?: Project["report_language"];
};

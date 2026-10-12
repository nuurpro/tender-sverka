"use client";

import { useEffect, useRef, useState } from "react";
import { WorkspaceApiError, type WorkspaceApi } from "@/lib/workspace-api";
import type { NewProject, Project } from "@/lib/workspace-types";

type Scope = { organizationId: string; created: Map<string, Project> };
const merge = (listed: Project[], created: Iterable<Project>) =>
  [...new Map([...listed, ...created].map(project => [project.id, project])).values()];

/** Each async response belongs to one mounted organization selection. */
export function useProjectWorkspace(api: WorkspaceApi, organizationId: string, onExpired: () => void) {
  const current = useRef<Scope | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const scope: Scope = { organizationId, created: new Map() };
    current.current = scope;
    setProjects([]); setError(""); setLoading(!!organizationId);
    if (organizationId) {
      api.projects(organizationId).then(items => {
        if (current.current === scope) setProjects(merge(items, scope.created.values()));
      }).catch(error => {
        if (current.current !== scope) return;
        if (error instanceof WorkspaceApiError && error.status === 401) onExpired();
        else setError(error instanceof Error ? error.message : "Не удалось загрузить проекты.");
      }).finally(() => { if (current.current === scope) setLoading(false); });
    }
    return () => { if (current.current === scope) current.current = null; };
  }, [api, organizationId, onExpired]);

  function invalidate() { current.current = null; setProjects([]); setLoading(false); }

  async function createProject(input: NewProject): Promise<Project | null> {
    const scope = current.current;
    if (!scope?.organizationId) throw new WorkspaceApiError("Выберите организацию и повторите попытку.", 409);
    try {
      const project = await api.createProject(scope.organizationId, input);
      if (current.current !== scope) return null;
      scope.created.set(project.id, project);
      setProjects(items => merge(items, [project]));
      setLoading(false);
      return project;
    } catch (error) {
      if (current.current === scope && error instanceof WorkspaceApiError && error.status === 401) onExpired();
      throw error;
    }
  }
  return { projects, loading, error, invalidate, createProject };
}

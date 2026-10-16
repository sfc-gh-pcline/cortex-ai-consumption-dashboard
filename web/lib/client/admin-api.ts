// Fetchers for the admin routes. Types come from lib/admin/actions so the client
// bundle never touches the server modules.
import type {
  LifecycleAction,
  LifecycleReport,
  ProvisionReport,
  SetupDraft,
} from "@/lib/admin/actions";
import { ApiError } from "@/lib/client/api";
import type { Settings } from "@/lib/settings";

export interface AdminState {
  readonly untracked: string[];
  readonly eligible: Record<"suspend" | "resume" | "refresh", string[]>;
  readonly provisionReport: ProvisionReport | null;
  readonly lifecycleReport: LifecycleReport | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { error?: string };
      if (body.error) detail = body.error;
    } catch {
      // keep statusText
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

const post = <T>(url: string, body: unknown) =>
  request<T>(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const fetchAdminState = () => request<AdminState>("/api/admin/state");
export const saveEnabled = (enabled: boolean) =>
  post<{ settings: Settings }>("/api/admin/settings", { enabled });
export const saveSetup = (draft: SetupDraft) =>
  post<{ settings: Settings }>("/api/admin/settings", draft);
export const provision = (retry: boolean) =>
  post<{ report: ProvisionReport }>("/api/admin/provision", { retry });
export const runAction = (action: LifecycleAction, targets: readonly string[], retry: boolean) =>
  post<{ report: LifecycleReport }>("/api/admin/action", { action, targets, retry });
export const adoptObjects = () => post<{ adopted: string[] }>("/api/admin/adopt", {});

// Typed fetchers for the route handlers, plus the response shapes they return.
// Client and server share the domain types in lib/*; only transport lives here.
import type { Row } from "@/lib/agg/rows";
import type { SurfaceStatus } from "@/lib/health";
import type { PreflightResult } from "@/lib/preflight";
import type { Settings } from "@/lib/settings";
import type { DateWindow, IsoDate, SourceCoverage } from "@/lib/view-state";

export interface PreflightResponse {
  readonly preflight: PreflightResult;
  readonly today: IsoDate | null;
  readonly viewer: string | null;
  readonly isAdmin: boolean;
}

export interface SettingsResponse {
  readonly settings: Settings;
  readonly location: { database: string; schema: string };
  readonly today: IsoDate;
  readonly statuses: Record<string, SurfaceStatus>;
  readonly readiness: Record<string, string>;
  readonly freshness: string;
  readonly healthy: boolean;
  readonly sourceModes: Record<string, [string, string]>;
  readonly generations: Record<string, string>;
  readonly sourceQueryCount: number;
}

export interface DependencyResponse {
  readonly dependency: string;
  readonly rows: Row[];
  readonly coverage: SourceCoverage;
  readonly degraded: boolean;
  readonly message: string | null;
  readonly rateSnapshotAt?: string | null;
  readonly generation: string;
  readonly window: DateWindow;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
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

export const fetchPreflight = () => getJson<PreflightResponse>("/api/preflight");

export const fetchSettings = (reload = false) =>
  getJson<SettingsResponse>(`/api/settings${reload ? "?reload=1" : ""}`);

export function fetchDependency(
  dependency: string,
  window: DateWindow,
  reload = false,
  direct = false,
  refresh = false,
): Promise<DependencyResponse> {
  const params = new URLSearchParams({ start: window.start, end: window.end });
  if (reload) params.set("reload", "1");
  if (direct) params.set("direct", "1");
  if (refresh) params.set("refresh", "1");
  return getJson<DependencyResponse>(
    `/api/dependency/${encodeURIComponent(dependency)}?${params.toString()}`,
  );
}

// Shared value contracts for scope, periods, coverage, and stable navigation.
// Port of utils/view_state.py. Dates are ISO calendar strings (YYYY-MM-DD) in UTC;
// never JavaScript Date objects, which carry a timezone.

export type IsoDate = string;

export const CoverageState = {
  AVAILABLE: "available",
  PARTIAL: "partial",
  UNAVAILABLE: "unavailable",
  UNKNOWN: "unknown",
} as const;
export type CoverageState = (typeof CoverageState)[keyof typeof CoverageState];

export const ComparisonState = {
  COMPARABLE: "comparable",
  ZERO_BASELINE: "zero_baseline",
  BOTH_ZERO: "both_zero",
  INCOMPLETE: "incomplete",
  UNAVAILABLE: "unavailable",
} as const;
export type ComparisonState = (typeof ComparisonState)[keyof typeof ComparisonState];

// Inclusive UTC calendar interval.
export interface DateWindow {
  readonly start: IsoDate;
  readonly end: IsoDate;
}

// Requested range with separate completed and partial-day intervals.
export interface ComparisonPeriods {
  readonly requested: DateWindow;
  readonly current: DateWindow | null;
  readonly previous: DateWindow | null;
  readonly partialToday: DateWindow | null;
}

// Selections retained across navigation, including page-specific columns.
export interface FilterSelection {
  readonly users: readonly string[];
  readonly models: readonly string[];
  readonly tags: readonly string[];
  readonly pageValues: Readonly<Record<string, readonly string[]>>;
}

export const EMPTY_SELECTION: FilterSelection = {
  users: [],
  models: [],
  tags: [],
  pageValues: {},
};

// Whether any dimension selection narrows the view.
export function selectionActive(selection: FilterSelection): boolean {
  return (
    selection.users.length > 0 ||
    selection.models.length > 0 ||
    selection.tags.length > 0 ||
    Object.values(selection.pageValues).some((values) => values.length > 0)
  );
}

// Metadata produced by the same read that produced a dependency's rows.
export interface SourceCoverage {
  readonly dependency: string;
  readonly actualSource: string;
  readonly fetchedAt: string | null;
  readonly requested: DateWindow;
  readonly served: DateWindow | null;
  readonly retentionStart?: IsoDate | null;
  readonly latencyHours?: number | null;
  readonly snapshotAt?: string | null;
  readonly state: CoverageState;
  readonly reason?: string | null;
  readonly readStartedAt?: string | null;
  readonly completeThrough?: IsoDate | null;
  readonly latestUsage?: IsoDate | null;
}

// Measured values plus an explicit comparison outcome.
export interface ComparisonResult {
  readonly current: number | null;
  readonly previous: number | null;
  readonly deltaPercent: number | null;
  readonly state: ComparisonState;
  readonly reason?: string | null;
}

// Stable route and dependency identity independent of a display title.
export interface PageIdentity {
  readonly pageId: string;
  readonly title: string;
  readonly dependencyKey: string;
  readonly group: string;
}

// Overview stays at the root; every other route keeps its Streamlit path.
export function urlPath(page: PageIdentity): string {
  return page.pageId === "total_consumption" ? "" : page.pageId;
}

export const PAGES: readonly PageIdentity[] = [
  { pageId: "total_consumption", title: "Overview", dependencyKey: "Total Consumption", group: "" },
  { pageId: "coco", title: "CoCo", dependencyKey: "CoCo", group: "AI Services" },
  { pageId: "cowork", title: "CoWork", dependencyKey: "CoWork", group: "AI Services" },
  {
    pageId: "cortex_agents",
    title: "Cortex Agents",
    dependencyKey: "Cortex Agents",
    group: "AI Services",
  },
  {
    pageId: "cortex_search",
    title: "Cortex Search",
    dependencyKey: "Cortex Search",
    group: "AI Services",
  },
  {
    pageId: "cortex_inference_api",
    title: "Cortex Inference API",
    dependencyKey: "Cortex Inference API",
    group: "AI Services",
  },
  {
    pageId: "cortex_ai_functions",
    title: "Cortex AI Functions",
    dependencyKey: "Cortex AI Functions",
    group: "AI Services",
  },
  {
    pageId: "warehouse_attribution",
    title: "Warehouse Attribution",
    dependencyKey: "Warehouse Attribution",
    group: "Infrastructure",
  },
  { pageId: "sql", title: "Reference SQL", dependencyKey: "Reference SQL", group: "Reference" },
  {
    pageId: "acceleration_sql",
    title: "Query Acceleration SQL",
    dependencyKey: "Query Acceleration SQL",
    group: "Reference",
  },
  {
    pageId: "config",
    title: "Acceleration Settings",
    dependencyKey: "Config",
    group: "Administration",
  },
];

// Which attribution columns each source can honestly filter on.
export const FILTER_CAPABILITIES: Readonly<Record<string, ReadonlySet<string>>> = {
  CoCo: new Set(["USER_NAME", "MODEL_NAME", "SUB_SURFACE", "ROLE_NAME"]),
  CoWork: new Set(["USER_NAME", "COWORK_NAME", "AGENT_FQN"]),
  "CoWork service mix": new Set(["USER_NAME"]),
  "Cortex Agents": new Set(["USER_NAME", "AGENT_FQN"]),
  "Cortex Search": new Set(["SERVICE_NAME", "CONSUMPTION_TYPE"]),
  "Cortex Inference API": new Set(["USER_NAME", "MODEL_NAME", "INFERENCE_REGION", "QUERY_TAG"]),
  "Cortex AI Functions": new Set(["USER_NAME", "MODEL_NAME", "FUNCTION_NAME", "QUERY_TAG"]),
  "Warehouse Attribution": new Set(["SURFACE", "WAREHOUSE_NAME", "TIER"]),
};

export function capabilitiesFor(dependencyKey: string): ReadonlySet<string> {
  return FILTER_CAPABILITIES[dependencyKey] ?? new Set<string>();
}

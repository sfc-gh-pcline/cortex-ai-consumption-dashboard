// Surface names, dependency classification, and the page-to-dependency registry.
// Port of the PAGE_REFRESH_PLANS section of utils/queries.py. The registry is the
// single source of truth for what each page loads and how its cache is keyed.

export const SURFACE_COCO = "CoCo";
export const SURFACE_COWORK = "CoWork";
export const SURFACE_AGENTS = "Cortex Agents";
export const SURFACE_REST_API = "Cortex Inference API";
export const SURFACE_AI_FUNCTIONS = "Cortex AI Functions";
export const SURFACE_MODEL_RATES = "Model Rates";
export const SURFACE_SEARCH = "Cortex Search";
export const SURFACE_WAREHOUSE_ATTRIBUTION = "Warehouse Attribution";

// Order matters: it drives chart color assignment and the overview pill order.
export const SURFACES: readonly string[] = [
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_AGENTS,
  SURFACE_SEARCH,
  SURFACE_REST_API,
  SURFACE_AI_FUNCTIONS,
];

export const COCO_SUB_SURFACES: readonly string[] = ["cli", "desktop", "snowsight"];

export const DEP_COWORK_SERVICE_MIX = `${SURFACE_COWORK} — Service Mix`;
export const DEP_SEARCH_DAILY = `${SURFACE_SEARCH} — Daily`;
export const DEP_SEARCH_SERVING = `${SURFACE_SEARCH} — Serving`;
export const DEP_SEARCH_BATCH = `${SURFACE_SEARCH} — Batch`;
export const DEP_USER_TAGS = "user_tags";

// Display names for dependency keys that are not already human-readable
// (utils/queries.py DEPENDENCY_LABELS).
export const DEPENDENCY_LABELS: Readonly<Record<string, string>> = { [DEP_USER_TAGS]: "User tags" };
export const dependencyLabel = (dependency: string): string =>
  DEPENDENCY_LABELS[dependency] ?? dependency;

export const PAGE_TOTAL_CONSUMPTION = "Total Consumption";
export const PAGE_SQL = "Reference SQL";
export const PAGE_QUERY_ACCELERATION_SQL = "Query Acceleration SQL";
export const PAGE_CONFIG = "Config";

export type DepKind = "windowed_aggregate" | "global_cached" | "direct_only";

// Every recognized dependency key mapped to its classification.
export const DEPENDENCY_KINDS: Readonly<Record<string, DepKind>> = {
  [SURFACE_COCO]: "windowed_aggregate",
  [SURFACE_AI_FUNCTIONS]: "windowed_aggregate",
  [SURFACE_AGENTS]: "windowed_aggregate",
  [SURFACE_COWORK]: "windowed_aggregate",
  [DEP_COWORK_SERVICE_MIX]: "windowed_aggregate",
  [DEP_SEARCH_DAILY]: "windowed_aggregate",
  [DEP_SEARCH_SERVING]: "windowed_aggregate",
  [DEP_SEARCH_BATCH]: "windowed_aggregate",
  [SURFACE_WAREHOUSE_ATTRIBUTION]: "windowed_aggregate",
  [SURFACE_MODEL_RATES]: "global_cached",
  [DEP_USER_TAGS]: "global_cached",
  [SURFACE_REST_API]: "direct_only",
};

// Surfaces the acceleration layer materializes (the ten scheduled tables).
export const DT_SURFACE_KEYS: readonly string[] = [
  SURFACE_COCO,
  SURFACE_AI_FUNCTIONS,
  SURFACE_AGENTS,
  SURFACE_COWORK,
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  DEP_SEARCH_BATCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
  SURFACE_MODEL_RATES,
];

export interface RefreshPlan {
  readonly pageKey: string;
  readonly windowed: readonly string[];
  readonly globalCached: readonly string[];
  readonly directOnly: readonly string[];
  readonly usesSettledWindow: boolean;
}

function plan(
  pageKey: string,
  windowed: readonly string[],
  globalCached: readonly string[],
  directOnly: readonly string[],
  usesSettledWindow: boolean,
): RefreshPlan {
  return { pageKey, windowed, globalCached, directOnly, usesSettledWindow };
}

// Maps every navigable page to exactly one declared RefreshPlan.
export const PAGE_REFRESH_PLANS: Readonly<Record<string, RefreshPlan>> = {
  [PAGE_TOTAL_CONSUMPTION]: plan(
    PAGE_TOTAL_CONSUMPTION,
    [SURFACE_COCO, SURFACE_COWORK, SURFACE_AGENTS, SURFACE_AI_FUNCTIONS, DEP_SEARCH_DAILY],
    [SURFACE_MODEL_RATES, DEP_USER_TAGS],
    [SURFACE_REST_API],
    true,
  ),
  [SURFACE_COCO]: plan(
    SURFACE_COCO,
    [SURFACE_COCO, SURFACE_WAREHOUSE_ATTRIBUTION],
    [DEP_USER_TAGS],
    [],
    true,
  ),
  [SURFACE_COWORK]: plan(
    SURFACE_COWORK,
    [SURFACE_COWORK, DEP_COWORK_SERVICE_MIX, SURFACE_WAREHOUSE_ATTRIBUTION],
    [DEP_USER_TAGS],
    [],
    true,
  ),
  [SURFACE_AGENTS]: plan(
    SURFACE_AGENTS,
    [SURFACE_AGENTS, SURFACE_WAREHOUSE_ATTRIBUTION],
    [DEP_USER_TAGS],
    [],
    true,
  ),
  [SURFACE_SEARCH]: plan(
    SURFACE_SEARCH,
    [DEP_SEARCH_DAILY, DEP_SEARCH_SERVING, DEP_SEARCH_BATCH, SURFACE_WAREHOUSE_ATTRIBUTION],
    [],
    [],
    true,
  ),
  [SURFACE_REST_API]: plan(
    SURFACE_REST_API,
    [],
    [SURFACE_MODEL_RATES, DEP_USER_TAGS],
    [SURFACE_REST_API],
    false,
  ),
  [SURFACE_AI_FUNCTIONS]: plan(
    SURFACE_AI_FUNCTIONS,
    [SURFACE_AI_FUNCTIONS, SURFACE_WAREHOUSE_ATTRIBUTION],
    [DEP_USER_TAGS],
    [],
    true,
  ),
  [SURFACE_WAREHOUSE_ATTRIBUTION]: plan(
    SURFACE_WAREHOUSE_ATTRIBUTION,
    [SURFACE_WAREHOUSE_ATTRIBUTION],
    [],
    [],
    true,
  ),
  [PAGE_SQL]: plan(PAGE_SQL, [], [], [], false),
  [PAGE_QUERY_ACCELERATION_SQL]: plan(PAGE_QUERY_ACCELERATION_SQL, [], [], [], false),
  [PAGE_CONFIG]: plan(PAGE_CONFIG, [], [], [], false),
};

const EMPTY_PLAN = plan("", [], [], [], false);

// Declared page dependencies in load order, de-duplicated.
export function warmDependenciesFor(pageKey: string): string[] {
  const p = PAGE_REFRESH_PLANS[pageKey] ?? EMPTY_PLAN;
  return [...new Set([...p.windowed, ...p.globalCached, ...p.directOnly])];
}

// Explicit reload retries usage reads only. Global metadata keeps its own TTL,
// especially Model Rates, which is the most expensive dashboard query.
export function reloadDependenciesFor(pageKey: string): string[] {
  const p = PAGE_REFRESH_PLANS[pageKey] ?? EMPTY_PLAN;
  return [...new Set([...p.windowed, ...p.directOnly])];
}

// Primary facts and filter metadata; detail controls own auxiliary reads.
export function initialDependenciesFor(pageKey: string): string[] {
  const optional = new Set([DEP_COWORK_SERVICE_MIX, DEP_SEARCH_SERVING, DEP_SEARCH_BATCH]);
  if (pageKey !== SURFACE_WAREHOUSE_ATTRIBUTION) optional.add(SURFACE_WAREHOUSE_ATTRIBUTION);
  return warmDependenciesFor(pageKey).filter((dep) => !optional.has(dep));
}

// The overview and Search page identities map to their main facts.
export function mainDependencyFor(pageKey: string): string {
  return pageKey === SURFACE_SEARCH ? DEP_SEARCH_DAILY : pageKey;
}

// Aggregate dependencies that define one page's cache generation.
export function aggregateDependenciesFor(pageKey: string): string[] {
  return warmDependenciesFor(pageKey).filter(
    (key) => DEPENDENCY_KINDS[key] === "windowed_aggregate",
  );
}

// Whether a page reads any data at all (documentation and settings pages do not).
export function pageHasData(pageKey: string): boolean {
  const p = PAGE_REFRESH_PLANS[pageKey];
  return (
    p !== undefined &&
    (p.windowed.length > 0 || p.globalCached.length > 0 || p.directOnly.length > 0)
  );
}

// Needed dependencies not already warm at their current generation, in order.
export function dependenciesToWarm(
  needed: readonly string[],
  warmed: Readonly<Record<string, string>>,
  generations: Readonly<Record<string, string>>,
): string[] {
  return needed.filter((dep) => warmed[dep] !== (generations[dep] ?? ""));
}

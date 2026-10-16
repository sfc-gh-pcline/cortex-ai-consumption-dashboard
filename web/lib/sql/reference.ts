// Reference SQL and Query Acceleration catalogues. Port of SQL_REFERENCE,
// ACCELERATION_SQL_REFERENCE, and reference_statement_for in utils/queries.py.
// Copy text is taken from the Python source verbatim.
import type { DateWindow, IsoDate } from "@/lib/view-state";
import { addDays, effectiveServedWindow } from "@/lib/dates";
import {
  DEPENDENCY_KINDS,
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_BATCH,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  DEP_USER_TAGS,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_SEARCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";
import type { Settings } from "@/lib/settings";
import {
  DT_BODY_REGISTRY,
  DT_COLUMNS,
  aggregateTableFqn,
  modelRatesDtSql,
} from "@/lib/sql/acceleration";
import { AU, COCO_USAGE_VIEW, type Statement } from "@/lib/sql/core";
import {
  type ModelRate,
  agentFactsSql,
  aiFnFactsSql,
  cocoFactsSql,
  coworkFactsSql,
  coworkServiceMixSql,
  modelRatesSql,
  restFactsSql,
  searchBatchFactsSql,
  searchDailyFactsSql,
  searchServingFactsSql,
  userTagsSql,
} from "@/lib/sql/facts";
import {
  meteringProbeSql,
  warehouseCreditFactsAttributionOnlySql,
  warehouseCreditFactsSql,
} from "@/lib/sql/warehouse";

export const SHARED = "Shared";
export const KIND_PRIMARY = "";
export const KIND_ADDITIONAL = "Additional fact";
export const KIND_SHARED_INPUT = "Shared input";
export const KIND_FALLBACK = "Fallback path";

// Inputs that window-independent or rate-dependent builders need.
export interface BuilderContext {
  readonly today: IsoDate;
  readonly rates: readonly ModelRate[];
}

export interface SqlReference {
  readonly label: string;
  readonly sources: readonly string[];
  readonly note: string;
  readonly builder: (start: IsoDate, end: IsoDate, context: BuilderContext) => Statement;
  readonly group: string;
  readonly variant: string;
  readonly kind: string;
  readonly runtimeKey: string | null;
}

function entry(
  label: string,
  sources: readonly string[],
  note: string,
  builder: SqlReference["builder"],
  group: string,
  variant: string,
  options: { kind?: string; runtimeKey?: string | null } = {},
): SqlReference {
  return {
    label,
    sources,
    note,
    builder,
    group,
    variant,
    kind: options.kind ?? KIND_PRIMARY,
    runtimeKey: options.runtimeKey ?? null,
  };
}

export const SQL_REFERENCE: readonly SqlReference[] = [
  entry(
    SURFACE_COCO,
    [`${AU}.${COCO_USAGE_VIEW}`],
    "One aggregate row per delivery surface, day, user, model, role and " +
      "region. The unified view is flattened by model and token/credit model " +
      "keys are paired before aggregation.",
    cocoFactsSql,
    SURFACE_COCO,
    "Fact grain",
    { runtimeKey: SURFACE_COCO },
  ),
  entry(
    SURFACE_COWORK,
    [`${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY`],
    "Fact grain for CoWork. METADATA carries sql_query_credits and " +
      "ai_functions_credits, which are billed on top of TOKEN_CREDITS, so they " +
      "are kept as their own columns rather than folded into credits.",
    coworkFactsSql,
    SURFACE_COWORK,
    "Fact grain",
    { runtimeKey: SURFACE_COWORK },
  ),
  entry(
    DEP_COWORK_SERVICE_MIX,
    [`${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY`],
    "CoWork-only breakdown. TOKENS_GRANULAR and CREDITS_GRANULAR are ARRAYs " +
      "nested request_id -> service_type -> model, so four FLATTEN levels are " +
      "needed to split one turn across cortex_agents and its tool calls.",
    coworkServiceMixSql,
    SURFACE_COWORK,
    "Service mix",
    { kind: KIND_ADDITIONAL, runtimeKey: DEP_COWORK_SERVICE_MIX },
  ),
  entry(
    SURFACE_AGENTS,
    [`${AU}.CORTEX_AGENT_USAGE_HISTORY`],
    "One row per agent request. PARENT_REQUEST_ID separates root requests " +
      "(one per user turn) from child requests the agent spawns while " +
      "servicing that turn.",
    agentFactsSql,
    SURFACE_AGENTS,
    "Fact grain",
    { runtimeKey: SURFACE_AGENTS },
  ),
  entry(
    SURFACE_SEARCH,
    [`${AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY`],
    "Daily usage by CONSUMPTION_TYPE, SERVICE_NAME and MODEL_NAME. USAGE_DATE is " +
      "TIMESTAMP_LTZ here (not DATE, unlike most other surfaces), so it is bucketed " +
      "through the same UTC-day helper as everything else.",
    searchDailyFactsSql,
    SURFACE_SEARCH,
    "Daily",
    { runtimeKey: DEP_SEARCH_DAILY },
  ),
  entry(
    DEP_SEARCH_SERVING,
    [`${AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY`],
    "Hourly query-time serving credits by SERVICE_NAME. No CONSUMPTION_TYPE or " +
      "TOKENS column on this view -- serving is credits-only.",
    searchServingFactsSql,
    SURFACE_SEARCH,
    "Serving",
    { kind: KIND_ADDITIONAL, runtimeKey: DEP_SEARCH_SERVING },
  ),
  entry(
    DEP_SEARCH_BATCH,
    [`${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY`],
    "Batch (AI_COMPLETE-style) query credits by QUERY_ID. Uses CREDITS_USED, not " +
      "CREDITS like the other two Search views, and START_TIME here is " +
      "TIMESTAMP_TZ, not TIMESTAMP_LTZ. Joins to the Warehouse Attribution fact by " +
      "QUERY_ID on the Cortex Search page for per-query warehouse cost.",
    searchBatchFactsSql,
    SURFACE_SEARCH,
    "Batch",
    { kind: KIND_ADDITIONAL, runtimeKey: DEP_SEARCH_BATCH },
  ),
  entry(
    SURFACE_REST_API,
    [`${AU}.CORTEX_REST_API_USAGE_HISTORY`, `${AU}.USERS`],
    "This view reports tokens but no credits, so credits are ESTIMATED by " +
      "applying per-model, per-token-type rates in the model_rates CTE. Those " +
      "rates are observed from CoCo history and bound as literals.",
    (start, end, context) => restFactsSql(start, end, context.rates),
    SURFACE_REST_API,
    "Fact grain",
    { runtimeKey: SURFACE_REST_API },
  ),
  entry(
    SURFACE_AI_FUNCTIONS,
    [`${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY`, `${AU}.USERS`],
    "Credits come straight from the view. Tokens are summed out of the " +
      "METRICS array, filtered to entries whose unit is 'tokens', so functions " +
      "billed per page or per row report credits with zero tokens.",
    aiFnFactsSql,
    SURFACE_AI_FUNCTIONS,
    "Fact grain",
    { runtimeKey: SURFACE_AI_FUNCTIONS },
  ),
  entry(
    `${SURFACE_WAREHOUSE_ATTRIBUTION} — Full`,
    [
      `${AU}.QUERY_ATTRIBUTION_HISTORY`,
      `${AU}.QUERY_METERING_HISTORY`,
      `${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY`,
      `${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY`,
    ],
    "Two-path loader step 2 of 3: the full statement, run when the probe succeeds. " +
      "Pre-aggregates each source view to one row per QUERY_ID before an " +
      "attribution-wins UNION ALL, then restricts to Cortex-attributable rows only " +
      "(a QUERY_TAG match or a QUERY_ID present in a Cortex usage view).",
    warehouseCreditFactsSql,
    SURFACE_WAREHOUSE_ATTRIBUTION,
    "Full",
    { runtimeKey: SURFACE_WAREHOUSE_ATTRIBUTION },
  ),
  entry(
    `${SURFACE_WAREHOUSE_ATTRIBUTION} — Probe`,
    [`${AU}.QUERY_METERING_HISTORY`],
    "Two-path loader step 1 of 3: a zero-row availability probe run before the " +
      "main statement, so a privilege or region error on QUERY_METERING_HISTORY " +
      "surfaces here rather than silently masking valid attribution rows.",
    () => meteringProbeSql(),
    SURFACE_WAREHOUSE_ATTRIBUTION,
    "Probe",
    { kind: KIND_FALLBACK },
  ),
  entry(
    `${SURFACE_WAREHOUSE_ATTRIBUTION} — Attribution-only fallback`,
    [
      `${AU}.QUERY_ATTRIBUTION_HISTORY`,
      `${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY`,
      `${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY`,
    ],
    "Two-path loader step 3 of 3: run when the probe fails (privilege or region " +
      "error), so standard-warehouse spend is never dropped just because " +
      "Adaptive-warehouse metering is unavailable.",
    warehouseCreditFactsAttributionOnlySql,
    SURFACE_WAREHOUSE_ATTRIBUTION,
    "Attribution-only",
    { kind: KIND_FALLBACK },
  ),
  entry(
    "Model Credit Rates",
    [`${AU}.${COCO_USAGE_VIEW}`],
    "Scanned once over a bounded 90-day window and reused as bound literals " +
      "inside the REST API query, so rate collection does not repeat per page.",
    (_start, _end, context) => modelRatesSql(context.today),
    SHARED,
    "Model credit rates",
    { kind: KIND_SHARED_INPUT, runtimeKey: SURFACE_MODEL_RATES },
  ),
  entry(
    "User Tags",
    [`${AU}.TAG_REFERENCES`],
    "Current USER-domain tag references. The per-row USER_TAGS column on the " +
      "usage views is populated on too few rows to filter on reliably.",
    () => ({ sql: userTagsSql(), params: [] }),
    SHARED,
    "User tags",
    { kind: KIND_SHARED_INPUT, runtimeKey: DEP_USER_TAGS },
  ),
];

// Page-level groups in navigation order, excluding Shared.
export function sqlReferenceGroups(): string[] {
  return [...new Set(SQL_REFERENCE.filter((e) => e.group !== SHARED).map((e) => e.group))];
}

export function sqlReferenceFor(group: string): SqlReference[] {
  return SQL_REFERENCE.filter((e) => e.group === group);
}

export type SourceMode = "aggregate" | "direct" | "global_cached" | "fallback";

export interface ReferenceStatement {
  readonly sql: string;
  readonly params: readonly (string | number | null)[];
  readonly sourceMode: SourceMode;
  readonly sourceLabel: string;
  readonly served: DateWindow | null;
  readonly materializationKey: string | null;
}

// Resolves the statement the dashboard actually runs for a reference entry, using
// the same source routing as the loaders. `sourceFor` reports "dt" when a surface
// is served from its aggregate table.
export function referenceStatementFor(
  ref: SqlReference,
  window: DateWindow,
  settings: Settings,
  context: BuilderContext & { sourceFor: (key: string) => "dt" | "account_usage" },
): ReferenceStatement {
  const key = ref.runtimeKey;
  if (key === null) {
    const s = ref.builder(window.start, window.end, context);
    return {
      sql: s.sql,
      params: s.params,
      sourceMode: "fallback",
      sourceLabel: "Conditional fallback or availability probe",
      served: null,
      materializationKey: null,
    };
  }
  if (key in DT_BODY_REGISTRY && context.sourceFor(key) === "dt") {
    if (key === SURFACE_MODEL_RATES) {
      const s = modelRatesDtSql(settings);
      return {
        sql: s.sql,
        params: s.params,
        sourceMode: "aggregate",
        sourceLabel: "Global aggregate table",
        served: null,
        materializationKey: key,
      };
    }
    const served = effectiveServedWindow(window, { accelerated: true, today: context.today });
    const cols = DT_COLUMNS[key].join(",\n    ");
    const sql =
      `SELECT\n    ${cols}\n` +
      `FROM ${aggregateTableFqn(key, settings)}\n` +
      "WHERE USAGE_DATE >= ?::DATE\n" +
      "  AND USAGE_DATE < ?::DATE";
    return {
      sql,
      params: [served.start, addDays(served.end, 1)],
      sourceMode: "aggregate",
      sourceLabel: "Scheduled aggregate table",
      served,
      materializationKey: key,
    };
  }
  if (DEPENDENCY_KINDS[key] === "global_cached") {
    const s = ref.builder(window.start, window.end, context);
    return {
      sql: s.sql,
      params: s.params,
      sourceMode: "global_cached",
      sourceLabel:
        key === SURFACE_MODEL_RATES
          ? "Global cached model-rate table"
          : "Current USER-domain metadata",
      served: null,
      materializationKey: null,
    };
  }
  const s = ref.builder(window.start, window.end, context);
  return {
    sql: s.sql,
    params: s.params,
    sourceMode: "direct",
    sourceLabel: "Live SNOWFLAKE.ACCOUNT_USAGE",
    served: window,
    materializationKey: null,
  };
}

// ---------------------------------------------------------------------------
// Query acceleration educational catalogue
// ---------------------------------------------------------------------------

export interface AccelerationSqlReference {
  readonly surfaceKey: string;
  readonly objectType: "WINDOWED_AGGREGATE" | "GLOBAL_AGGREGATE";
  readonly sourceViews: readonly string[];
  readonly explanation: string;
}

const ACCELERATION_EXPLANATIONS: Readonly<Record<string, string>> = {
  [SURFACE_COCO]:
    "Flattens the unified CoCo usage view by token/credit model and aggregates to one row " +
    "per surface, day, user, model, role and region so the CoCo page reads a small table " +
    "instead of re-flattening history on every load.",
  [SURFACE_AI_FUNCTIONS]:
    "Pre-sums the METRICS token array and credits per function, model and query tag so the " +
    "AI Functions page avoids the per-row REDUCE over ACCOUNT_USAGE.",
  [SURFACE_AGENTS]:
    "Rolls Cortex Agents requests up per agent and user, splitting root from child requests " +
    "via PARENT_REQUEST_ID.",
  [SURFACE_COWORK]:
    "Aggregates CoWork per instance, agent and user, keeping the separately-billed " +
    "sql_query_credits and ai_functions_credits as their own columns.",
  [DEP_COWORK_SERVICE_MIX]:
    "Splits each CoWork turn across service types and models by flattening TOKENS_GRANULAR " +
    "and CREDITS_GRANULAR four levels deep, then joining tokens to credits per request.",
  [DEP_SEARCH_DAILY]:
    "Daily Cortex Search usage by service, consumption type and model — the primary Search " +
    "fact and the one the Total Consumption overview reads.",
  [DEP_SEARCH_SERVING]:
    "Hourly query-time serving credits per service, for the Search page's serving trend.",
  [DEP_SEARCH_BATCH]:
    "Batch (AI_COMPLETE-style) query credits per QUERY_ID, joined to warehouse attribution " +
    "on the Search page for per-query compute cost.",
  [SURFACE_WAREHOUSE_ATTRIBUTION]:
    "Unifies QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY into one row per QUERY_ID, " +
    "classified to the Cortex surface that incurred it, for cross-surface warehouse cost.",
  [SURFACE_MODEL_RATES]:
    "A global per-model, per-token-type rate table computed over a fixed 90-day CoCo window. " +
    "The Cortex Inference API page multiplies these rates by REST token counts to estimate " +
    "credits. Not date-windowed, so it is refreshed and read as a whole.",
};

const RUNTIME_KEY_TO_REFERENCE = new Map(
  SQL_REFERENCE.filter((e) => e.runtimeKey !== null).map((e) => [e.runtimeKey as string, e]),
);

function accelerationSourceViews(surfaceKey: string): readonly string[] {
  return RUNTIME_KEY_TO_REFERENCE.get(surfaceKey)?.sources ?? [`${AU}.${COCO_USAGE_VIEW}`];
}

export const ACCELERATION_SQL_REFERENCE: readonly AccelerationSqlReference[] = Object.keys(
  DT_BODY_REGISTRY,
).map((key) => ({
  surfaceKey: key,
  objectType: key === SURFACE_MODEL_RATES ? "GLOBAL_AGGREGATE" : "WINDOWED_AGGREGATE",
  sourceViews: accelerationSourceViews(key),
  explanation: ACCELERATION_EXPLANATIONS[key] ?? "",
}));

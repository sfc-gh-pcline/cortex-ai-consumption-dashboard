// Overview assembly: every surface normalised onto one row shape. Port of
// load_unified_facts in utils/queries.py, made pure: the client fetches the six
// dependencies and stacks them here, so no extra endpoint is needed.
import { isMissing, type Row } from "@/lib/agg/rows";
import { unavailableCoverage } from "@/lib/coverage";
import {
  DEP_SEARCH_DAILY,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_REST_API,
  SURFACE_SEARCH,
} from "@/lib/plans";
import { CoverageState, type DateWindow, type SourceCoverage } from "@/lib/view-state";

export const METRIC_COLUMNS = ["CALLS", "TOKENS", "CREDITS"] as const;

// Column order of the unified rows, populated or empty.
export const UNIFIED_COLUMNS = [
  "USAGE_DATE",
  "USER_NAME",
  "MODEL_NAME",
  "SUB_SURFACE",
  "SURFACE",
  "CREDITS_ESTIMATED",
  "RATE_INCOMPLETE",
  "UNRATED_TOKENS",
  ...METRIC_COLUMNS,
] as const;

export interface UnifiedSource {
  readonly surface: string;
  readonly dependency: string;
  readonly dimensions: readonly string[];
  readonly creditsEstimated: boolean;
}

// CoWork is additive, not a duplicate: requests originating from Cortex Agents
// land in CORTEX_AGENT_USAGE_HISTORY and are excluded from the CoWork view.
export const UNIFIED_SOURCES: readonly UnifiedSource[] = [
  {
    surface: SURFACE_COCO,
    dependency: SURFACE_COCO,
    dimensions: ["MODEL_NAME", "SUB_SURFACE"],
    creditsEstimated: false,
  },
  {
    surface: SURFACE_AI_FUNCTIONS,
    dependency: SURFACE_AI_FUNCTIONS,
    dimensions: ["MODEL_NAME"],
    creditsEstimated: false,
  },
  {
    surface: SURFACE_REST_API,
    dependency: SURFACE_REST_API,
    dimensions: ["MODEL_NAME"],
    creditsEstimated: true,
  },
  { surface: SURFACE_AGENTS, dependency: SURFACE_AGENTS, dimensions: [], creditsEstimated: false },
  { surface: SURFACE_COWORK, dependency: SURFACE_COWORK, dimensions: [], creditsEstimated: false },
  {
    surface: SURFACE_SEARCH,
    dependency: DEP_SEARCH_DAILY,
    dimensions: ["MODEL_NAME"],
    creditsEstimated: false,
  },
];

// One dependency's outcome as the client received it.
export interface SourceResult {
  readonly rows: readonly Row[];
  readonly coverage: SourceCoverage;
}

export interface UnifiedFacts {
  readonly rows: Row[];
  readonly unavailableSources: string[];
  readonly coverages: SourceCoverage[];
}

// Stacks the six sources; a missing result records an unavailable coverage.
export function unifyFacts(
  results: Readonly<Record<string, SourceResult | null | undefined>>,
  window: DateWindow,
): UnifiedFacts {
  const rows: Row[] = [];
  const unavailable: string[] = [];
  const coverages: SourceCoverage[] = [];
  for (const source of UNIFIED_SOURCES) {
    const result = results[source.dependency];
    if (!result) {
      unavailable.push(source.surface);
      coverages.push(unavailableCoverage(source.dependency, window, null));
      continue;
    }
    coverages.push(result.coverage);
    if (result.coverage.state === CoverageState.UNAVAILABLE) unavailable.push(source.surface);
    for (const fact of result.rows) {
      const row: Row = {
        USAGE_DATE: fact.USAGE_DATE,
        USER_NAME: "USER_NAME" in fact ? fact.USER_NAME : null,
        MODEL_NAME: source.dimensions.includes("MODEL_NAME") ? fact.MODEL_NAME : null,
        SUB_SURFACE: source.dimensions.includes("SUB_SURFACE") ? fact.SUB_SURFACE : null,
        SURFACE: source.surface,
        CREDITS_ESTIMATED: source.creditsEstimated,
        RATE_INCOMPLETE: source.creditsEstimated
          ? isMissing(fact.RATE_INCOMPLETE)
            ? true
            : fact.RATE_INCOMPLETE
          : false,
        UNRATED_TOKENS: source.creditsEstimated
          ? isMissing(fact.UNRATED_TOKENS)
            ? null
            : fact.UNRATED_TOKENS
          : 0,
      };
      for (const metric of METRIC_COLUMNS) row[metric] = fact[metric];
      rows.push(row);
    }
  }
  return { rows, unavailableSources: unavailable, coverages };
}

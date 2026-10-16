// URL-bound view state. Every control the Streamlit app bound with
// bind="query-params" keeps the same parameter name here, so links copied from
// either app resolve in the other: ?range=30d&users=A&users=B, plus the
// page-local keys in PAGE_FILTER_KEYS. Defaults are dropped from the URL.
"use client";

import {
  createMultiParser,
  parseAsBoolean,
  parseAsString,
  useQueryState,
  useQueryStates,
} from "nuqs";

import { DEFAULT_PRESET } from "@/lib/dates";

export function parseListValues(values: readonly string[]): string[] {
  return [...new Set(values.flatMap((value) => value.split(",")).filter(Boolean))];
}

export function serializeListValues(key: string, values: readonly string[]): URLSearchParams {
  const params = new URLSearchParams();
  for (const value of values) params.append(key, value);
  return params;
}

export const stringList = createMultiParser<string[]>({
  parse: parseListValues,
  serialize: (values) => values,
  eq: (a, b) => a.length === b.length && a.every((value, index) => value === b[index]),
}).withDefault([]);

// Page-local filter keys, mirroring PAGE_FILTER_KEYS in utils/ui.py.
export const PAGE_FILTER_KEYS: Readonly<Record<string, readonly string[]>> = {
  coco: ["coco_subs", "coco_roles"],
  cowork: ["cowork_instances", "cowork_agents"],
  cortex_agents: ["agent_fqns"],
  cortex_inference_api: ["rest_regions", "rest_tags"],
  cortex_ai_functions: ["aifn_functions", "aifn_tags"],
  cortex_search: ["search_services", "search_types"],
  warehouse_attribution: ["warehouse_surfaces", "warehouse_names", "warehouse_tiers"],
};

// Breakdown group-by defaults, mirroring PAGE_BREAKDOWN_DEFAULTS.
export const PAGE_BREAKDOWN_DEFAULTS: Readonly<Record<string, [key: string, value: string]>> = {
  coco: ["coco_breakdown", "By user"],
  cowork: ["cowork_breakdown", "By agent"],
  cortex_agents: ["agent_breakdown", "By agent"],
  cortex_inference_api: ["rest_breakdown", "By model"],
  cortex_ai_functions: ["aifn_breakdown", "By function"],
};

export function useDateParams() {
  return useQueryStates({
    range: parseAsString.withDefault(DEFAULT_PRESET),
    start: parseAsString,
    end: parseAsString,
    dates: createMultiParser<string[]>({
      parse: parseListValues,
      serialize: (values) => values,
    }),
  });
}

export function useGlobalFilters() {
  return useQueryStates({ users: stringList, models: stringList, tags: stringList });
}

export function useListParam(key: string) {
  return useQueryState(key, stringList);
}

export function useStringParam(key: string, fallback: string) {
  return useQueryState(key, parseAsString.withDefault(fallback));
}

export function useBooleanParam(key: string) {
  return useQueryState(key, parseAsBoolean.withDefault(false));
}

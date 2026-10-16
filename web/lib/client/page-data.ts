"use client";

// Per-page data assembly: load the page's declared dependencies for the
// current-plus-prior window, build the union facts, derive filter options, and
// resolve tag users. Port of the "filter sidebar part 2" and load_period_usage
// logic in streamlit_app.py / utils/ui.py.
import { useMemo } from "react";

import { options } from "@/lib/agg/rows";
import { tagLabelOptions, usersForTags } from "@/lib/agg/scope";
import { unifyFacts, type SourceResult } from "@/lib/agg/unified";
import type { Row } from "@/lib/agg/rows";
import { useDashboard } from "@/lib/client/dashboard";
import { useDependencies, useReload, type DependencyState } from "@/lib/client/queries";
import {
  DEP_USER_TAGS,
  PAGE_TOTAL_CONSUMPTION,
  initialDependenciesFor,
  mainDependencyFor,
  reloadDependenciesFor,
} from "@/lib/plans";
import { CoverageState, capabilitiesFor, type SourceCoverage } from "@/lib/view-state";

export interface PageFacts {
  readonly states: readonly DependencyState[];
  readonly loading: boolean;
  // Union rows for the load window (prior period through display end), or null when unavailable.
  readonly rows: Row[] | null;
  readonly coverages: SourceCoverage[];
  readonly unavailableSources: string[];
  readonly degradedMessage: string | null;
  readonly rateSnapshotAt: string | null;
  readonly tags: Row[] | null;
  readonly tagUsers: string[] | null;
  readonly userOptions: string[];
  readonly modelOptions: string[];
  readonly tagOptions: string[];
  readonly capabilities: ReadonlySet<string>;
  readonly reload: () => Promise<void>;
}

export function usePageFacts(pageKey: string): PageFacts {
  const { loadWindow, settings, filters, preflight } = useDashboard();
  const dependencies = useMemo(() => initialDependenciesFor(pageKey), [pageKey]);
  const states = useDependencies(dependencies, loadWindow, settings);
  const reloadAll = useReload();
  const byDependency = new Map(states.map((s) => [s.dependency, s]));
  const loading = states.some((s) => s.isLoading);

  const assembled = useMemo(() => {
    if (loading || !loadWindow) {
      return {
        rows: null as Row[] | null,
        coverages: [] as SourceCoverage[],
        unavailable: [] as string[],
      };
    }
    if (pageKey === PAGE_TOTAL_CONSUMPTION) {
      const results: Record<string, SourceResult | null> = {};
      for (const state of states) {
        results[state.dependency] = state.data
          ? { rows: state.data.rows, coverage: state.data.coverage }
          : null;
      }
      const unified = unifyFacts(results, loadWindow);
      return {
        rows: unified.rows,
        coverages: unified.coverages,
        unavailable: unified.unavailableSources,
      };
    }
    const main = states.find((s) => s.dependency === mainDependencyFor(pageKey));
    if (!main?.data) return { rows: null, coverages: [], unavailable: [pageKey] };
    const unavailable = main.data.coverage.state === CoverageState.UNAVAILABLE;
    return {
      rows: unavailable ? null : main.data.rows,
      coverages: [main.data.coverage],
      unavailable: unavailable ? [pageKey] : [],
    };
  }, [loading, loadWindow, pageKey, states]);

  const main = byDependency.get(mainDependencyFor(pageKey));
  const capabilities =
    pageKey === PAGE_TOTAL_CONSUMPTION
      ? new Set(["USER_NAME", "MODEL_NAME"])
      : capabilitiesFor(pageKey);
  const tagLookup = capabilities.has("USER_NAME") ? byDependency.get(DEP_USER_TAGS)?.data : null;
  const tags =
    tagLookup?.coverage.state === CoverageState.UNAVAILABLE ? null : (tagLookup?.rows ?? null);
  const tagUsers =
    filters.tags.length > 0 && tags !== null ? usersForTags(tags, filters.tags) : null;

  return {
    states,
    loading,
    rows: assembled.rows,
    coverages: assembled.coverages,
    unavailableSources: assembled.unavailable,
    degradedMessage: main?.data?.message ?? null,
    rateSnapshotAt: main?.data?.rateSnapshotAt ?? null,
    tags,
    tagUsers,
    userOptions: options(assembled.rows, "USER_NAME"),
    modelOptions: options(assembled.rows, "MODEL_NAME"),
    tagOptions: tagLabelOptions(tags),
    capabilities,
    reload: async () => {
      if (loadWindow)
        await reloadAll(reloadDependenciesFor(pageKey), loadWindow, preflight?.isAdmin ?? false);
    },
  };
}

// Loads one auxiliary dependency (warehouse, service mix, search detail) on demand.
export function useAuxiliary(dependency: string, enabled: boolean) {
  const { loadWindow, settings } = useDashboard();
  const [state] = useDependencies([dependency], loadWindow, settings, enabled);
  return state;
}

"use client";

// React Query hooks over the route handlers. One query per dependency per
// window per aggregate generation, mirroring the Streamlit acquisition keys, so a
// nightly task refresh invalidates only the pages that read that surface.
import { useMemo } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchDependency,
  fetchPreflight,
  fetchSettings,
  type DependencyResponse,
  type SettingsResponse,
} from "@/lib/client/api";
import type { DateWindow } from "@/lib/view-state";

export const preflightKey = ["preflight"] as const;
export const settingsKey = ["settings"] as const;

export function dependencyKey(dependency: string, window: DateWindow, generation: string) {
  return ["dependency", dependency, window.start, window.end, generation] as const;
}

export function usePreflight() {
  return useQuery({ queryKey: preflightKey, queryFn: fetchPreflight, staleTime: 5 * 60_000 });
}

export function useSettings() {
  return useQuery({
    queryKey: settingsKey,
    queryFn: () => fetchSettings(false),
    staleTime: 60_000,
  });
}

export interface DependencyState {
  readonly dependency: string;
  readonly data: DependencyResponse | undefined;
  readonly isLoading: boolean;
  readonly error: string | null;
}

// Loads several dependencies in parallel for one window.
export function useDependencies(
  dependencies: readonly string[],
  window: DateWindow | null,
  settings: SettingsResponse | undefined,
  enabled = true,
): DependencyState[] {
  const results = useQueries({
    queries: dependencies.map((dependency) => {
      const generation = settings?.generations[dependency] ?? "";
      const w = window ?? { start: "", end: "" };
      return {
        queryKey: dependencyKey(dependency, w, generation),
        queryFn: () => fetchDependency(dependency, w),
        enabled: enabled && window !== null && settings !== undefined,
        placeholderData: (previous: DependencyResponse | undefined) => previous,
      };
    }),
  });
  // useQueries returns a structurally shared array, so this only changes when a result does.
  return useMemo(
    () =>
      dependencies.map((dependency, index) => ({
        dependency,
        data: results[index].data,
        isLoading: results[index].isLoading || results[index].isPending,
        error: results[index].error ? String(results[index].error) : null,
      })),
    [dependencies, results],
  );
}

// Viewers may refetch the current view from the result cache. Admins may bypass
// that cache for the current view without invalidating settings or model rates.
export function useReload() {
  const client = useQueryClient();
  return async (dependencies: readonly string[], window: DateWindow, refresh = false) => {
    const settings = await client.fetchQuery({
      queryKey: settingsKey,
      queryFn: () => fetchSettings(false),
      staleTime: 0,
    });
    for (const dependency of dependencies) {
      const generation = settings.generations[dependency] ?? "";
      await client.fetchQuery({
        queryKey: dependencyKey(dependency, window, generation),
        queryFn: () => fetchDependency(dependency, window, false, false, refresh),
        staleTime: 0,
      });
    }
  };
}

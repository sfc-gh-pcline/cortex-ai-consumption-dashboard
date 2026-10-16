"use client";

// Shared dashboard state: the active page, Snowflake's UTC today, resolved
// comparison periods, global filters, and the settings/health snapshot. Port of
// the entrypoint logic in streamlit_app.py, with URL parameters as the store.
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { PreflightResponse, SettingsResponse } from "@/lib/client/api";
import { usePreflight, useSettings } from "@/lib/client/queries";
import { useDateParams, useGlobalFilters } from "@/lib/client/url-state";
import {
  CUSTOM,
  DEFAULT_PRESET,
  parseLegacyDates,
  resolveCustomInput,
  resolvePeriods,
} from "@/lib/dates";
import {
  PAGES,
  urlPath,
  type ComparisonPeriods,
  type DateWindow,
  type IsoDate,
  type PageIdentity,
} from "@/lib/view-state";

export interface DashboardState {
  readonly page: PageIdentity;
  readonly today: IsoDate | null;
  readonly preflight: PreflightResponse | undefined;
  readonly preflightError: string | null;
  readonly settings: SettingsResponse | undefined;
  readonly appliedRange: string;
  readonly periods: ComparisonPeriods | null;
  readonly periodsError: string | null;
  // The interval pages display: completed current period, or the requested one.
  readonly displayWindow: DateWindow | null;
  // The interval dependencies read: prior period start through the display end.
  readonly loadWindow: DateWindow | null;
  readonly filters: { users: string[]; models: string[]; tags: string[] };
  readonly setFilters: (
    next: Partial<{ users: string[]; models: string[]; tags: string[] }>,
  ) => void;
}

const DashboardContext = createContext<DashboardState | null>(null);

export function pageForPath(pathname: string): PageIdentity {
  const slug = pathname.replace(/^\/+|\/+$/g, "");
  return PAGES.find((p) => urlPath(p) === slug) ?? PAGES[0];
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const page = useMemo(() => pageForPath(pathname), [pathname]);
  const preflight = usePreflight();
  const settings = useSettings();
  const [dates, setDates] = useDateParams();
  const [filters, setFilterParams] = useGlobalFilters();
  const today = preflight.data?.today ?? null;
  const [appliedPeriods, setAppliedPeriods] = useState<ComparisonPeriods | null>(null);
  const [legacyError, setLegacyError] = useState<string | null>(null);

  // Legacy `dates` links carried both endpoints as repeated parameters; migrate
  // them once into start/end and drop the old key, exactly as the Streamlit app did.
  useEffect(() => {
    if (!today || dates.range !== CUSTOM || !dates.dates) return;
    if (dates.start === null && dates.end === null) {
      try {
        const window = parseLegacyDates(today, dates.dates);
        void setDates({ start: window.start, end: window.end, dates: null });
        return;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        queueMicrotask(() => setLegacyError(message));
      }
    }
    void setDates({ dates: null });
  }, [today, dates.range, dates.dates, dates.start, dates.end, setDates]);

  const resolved = useMemo(() => {
    if (!today) return { periods: null, error: null };
    const preset = dates.range || DEFAULT_PRESET;
    if (preset !== CUSTOM) {
      try {
        return { periods: resolvePeriods(today, preset), error: null };
      } catch (err) {
        return { periods: null, error: err instanceof Error ? err.message : String(err) };
      }
    }
    if (dates.start === null || dates.end === null) return { periods: null, error: null };
    try {
      return { periods: resolveCustomInput(today, [dates.start, dates.end]), error: null };
    } catch (err) {
      return { periods: null, error: err instanceof Error ? err.message : String(err) };
    }
  }, [today, dates.range, dates.start, dates.end]);

  const periods = resolved.periods;
  useEffect(() => {
    if (!resolved.periods) return;
    const next = resolved.periods;
    queueMicrotask(() => setAppliedPeriods(next));
  }, [resolved.periods]);
  const activePeriods = periods ?? appliedPeriods;
  const displayWindow = activePeriods ? (activePeriods.current ?? activePeriods.requested) : null;
  const loadWindow =
    activePeriods && displayWindow
      ? { start: activePeriods.previous?.start ?? displayWindow.start, end: displayWindow.end }
      : null;

  const value: DashboardState = {
    page,
    today,
    preflight: preflight.data,
    preflightError: preflight.error ? String(preflight.error) : null,
    settings: settings.data,
    appliedRange: dates.range || DEFAULT_PRESET,
    periods: activePeriods,
    periodsError: legacyError ?? resolved.error,
    displayWindow,
    loadWindow,
    filters: { users: filters.users, models: filters.models, tags: filters.tags },
    setFilters: (next) => void setFilterParams(next),
  };
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardState {
  const value = useContext(DashboardContext);
  if (!value) throw new Error("useDashboard must be used inside DashboardProvider");
  return value;
}

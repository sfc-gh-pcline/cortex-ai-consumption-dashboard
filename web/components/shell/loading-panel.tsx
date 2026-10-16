"use client";

import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";

import { Caption } from "@/components/kit/notice";
import type { SettingsResponse } from "@/lib/client/api";
import type { DependencyState } from "@/lib/client/queries";
import { effectiveServedWindow } from "@/lib/dates";
import { aggregateSnapshotIdentityFor } from "@/lib/health";
import { DEPENDENCY_KINDS, dependencyLabel } from "@/lib/plans";
import { CoverageState, type DateWindow, type IsoDate } from "@/lib/view-state";

// Per-dependency progress while a window loads, replacing the Streamlit dialog.
// Each dependency is one request, so outcomes appear as they land.
export function LoadingPanel({
  states,
  window,
  settings,
  today,
}: {
  states: readonly DependencyState[];
  window: DateWindow;
  settings: SettingsResponse | undefined;
  today: IsoDate;
}) {
  const aggregateDeps = states
    .map((s) => s.dependency)
    .filter((d) => DEPENDENCY_KINDS[d] === "windowed_aggregate");
  const [mode, freshness] = settings
    ? aggregateSnapshotIdentityFor(aggregateDeps, settings.settings, settings.statuses)
    : ["direct", ""];
  const served = effectiveServedWindow(window, { accelerated: mode === "aggregate", today });
  const directOnly =
    states.length > 0 && states.every((s) => DEPENDENCY_KINDS[s.dependency] === "direct_only");
  const caption = directOnly
    ? `Reading directly from ACCOUNT_USAGE for ${window.start} to ${window.end} (direct source, not aggregate-eligible).`
    : mode === "aggregate"
      ? `Loading aggregate snapshot for ${served.start} to ${served.end} (latest successful snapshot: ${freshness.slice(0, 19)}).`
      : `Querying ACCOUNT_USAGE for ${window.start} to ${window.end}.`;
  const done = states.filter((s) => !s.isLoading).length;
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border px-4 py-3"
      role="status"
      aria-live="polite"
    >
      <div className="text-sm font-medium">Loading data</div>
      <Caption>{caption}</Caption>
      <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${(done / Math.max(states.length, 1)) * 100}%` }}
        />
      </div>
      <ul className="flex flex-col gap-1 text-xs">
        {states.map((state) => {
          const unavailable =
            state.error !== null || state.data?.coverage.state === CoverageState.UNAVAILABLE;
          return (
            <li key={state.dependency} className="flex items-center gap-2">
              {state.isLoading ? (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />
              ) : unavailable ? (
                <CircleAlert className="size-3.5 text-destructive" aria-hidden />
              ) : (
                <CheckCircle2 className="size-3.5 text-success" aria-hidden />
              )}
              <span>{dependencyLabel(state.dependency)}</span>
              <span className="text-muted-foreground">
                {state.isLoading
                  ? "loading"
                  : unavailable
                    ? state.data?.coverage.reason || state.error || "unavailable"
                    : "loaded"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

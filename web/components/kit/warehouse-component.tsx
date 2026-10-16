"use client";

import { Metric } from "@/components/kit/empty-state";
import { Caption, Notice } from "@/components/kit/notice";
import { Switch } from "@/components/ui/switch";
import { warehouseCreditsForSurface } from "@/lib/agg/metrics";
import { periodSlice } from "@/lib/agg/periods";
import { useDashboard } from "@/lib/client/dashboard";
import { useAuxiliary } from "@/lib/client/page-data";
import { useBooleanParam } from "@/lib/client/url-state";
import { fmtCredits } from "@/lib/format";
import { SURFACE_WAREHOUSE_ATTRIBUTION } from "@/lib/plans";
import { CoverageState } from "@/lib/view-state";

// Auxiliary warehouse compute for one service, loaded only after an explicit
// toggle (ui.optional_warehouse + ui.warehouse_component).
export function OptionalWarehouse({ source }: { source: string }) {
  const [open, setOpen] = useBooleanParam(`detail_warehouse_${source}`);
  const state = useAuxiliary(SURFACE_WAREHOUSE_ATTRIBUTION, open);
  const { periods } = useDashboard();
  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={open}
          onCheckedChange={(checked) => void setOpen(checked)}
          aria-label="Attributed warehouse detail"
        />
        Attributed warehouse detail
      </label>
      {open && (
        <div className="flex flex-col gap-2">
          <h2>Attributed warehouse compute</h2>
          <Caption>
            Scope: service and selected dates only. User, model, tag, agent and other page filters
            do not narrow this component. Separate from AI credits; do not add it to CoWork SQL
            metadata credits, which can overlap.
          </Caption>
          {state.isLoading && <Caption>Loading warehouse attribution...</Caption>}
          {state.data && (
            <>
              {(state.data.degraded || state.data.coverage.state === CoverageState.PARTIAL) && (
                <Notice tone="warning">
                  {state.data.message ??
                    state.data.coverage.reason ??
                    "Warehouse source coverage is incomplete."}
                </Notice>
              )}
              {state.data.coverage.state === CoverageState.UNAVAILABLE ||
              (state.data.degraded && state.data.rows.length === 0) ? (
                <Metric label="Warehouse credits" value="Unavailable" />
              ) : (
                <Metric
                  label="Warehouse credits"
                  value={fmtCredits(
                    warehouseCreditsForSurface(
                      periods ? periodSlice(state.data.rows, periods.current) : state.data.rows,
                      source,
                    ).TOTAL,
                  )}
                />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

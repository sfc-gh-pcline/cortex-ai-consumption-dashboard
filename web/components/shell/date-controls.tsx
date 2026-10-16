"use client";

import { useState } from "react";

import { Caption, Notice } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { Button } from "@/components/ui/button";
import { useDashboard } from "@/lib/client/dashboard";
import { useDateParams } from "@/lib/client/url-state";
import { CUSTOM, PRESET_KEYS, addDays, resolveCustomInput, windowDays } from "@/lib/dates";

// Date presets and the custom form. The window decides which queries run, so
// this renders before any data gate, as in the Streamlit entrypoint.
export function DateControls() {
  const { today, periods, periodsError, displayWindow, appliedRange } = useDashboard();
  const [dates, setDates] = useDateParams();
  const [draftStart, setDraftStart] = useState(dates.start ?? "");
  const [draftEnd, setDraftEnd] = useState(dates.end ?? "");
  const [applyError, setApplyError] = useState<string | null>(null);
  const options = [...PRESET_KEYS, CUSTOM];
  const minDate = today ? addDays(today, -730) : undefined;
  const applied =
    periods !== null && draftStart === (dates.start ?? "") && draftEnd === (dates.end ?? "");

  return (
    <div className="flex flex-col gap-2">
      <Segmented
        label="Date range"
        options={options}
        value={options.includes(appliedRange) ? appliedRange : CUSTOM}
        onChange={(value) => void setDates({ range: value })}
      />
      {appliedRange === CUSTOM && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            try {
              if (!today) throw new Error("Snowflake's UTC date is unavailable.");
              resolveCustomInput(today, [draftStart || null, draftEnd || null]);
              setApplyError(null);
            } catch (err) {
              setApplyError(err instanceof Error ? err.message : String(err));
              return;
            }
            void setDates({ start: draftStart || null, end: draftEnd || null });
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Start date
            <input
              type="date"
              value={draftStart}
              min={minDate}
              max={today ?? undefined}
              onChange={(e) => setDraftStart(e.target.value)}
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            End date
            <input
              type="date"
              value={draftEnd}
              min={minDate}
              max={today ?? undefined}
              onChange={(e) => setDraftEnd(e.target.value)}
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            />
          </label>
          <Button type="submit" size="sm">
            Apply dates
          </Button>
          {!applied && periods !== null && (
            <Caption>Dates are not applied. Results retain the last valid applied range.</Caption>
          )}
        </form>
      )}
      {applyError && <Notice tone="error">{applyError}</Notice>}
      {periodsError && <Notice tone="error">{periodsError}</Notice>}
      {periods === null && !periodsError && appliedRange === CUSTOM && (
        <Notice>Select and apply a complete date range before loading usage.</Notice>
      )}
      {periods !== null && periods.current === null && (
        <Notice>Today is partial and has no completed-period comparison.</Notice>
      )}
      {displayWindow && (
        <Caption>
          {displayWindow.start} to {displayWindow.end} UTC |{" "}
          {periods === null
            ? "Reference dates only. No usage range has been applied."
            : `${windowDays(displayWindow)} completed days`}
        </Caption>
      )}
    </div>
  );
}

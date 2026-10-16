"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";
import type { EmbedOptions, VisualizationSpec } from "vega-embed";

import { useIsDark } from "@/components/shell/theme-toggle";
import type { VegaSpec } from "@/lib/chart-spec";

// vega-embed touches the DOM at import time, so it loads client-side only.
const VegaEmbed = dynamic(() => import("react-vega").then((m) => m.VegaEmbed), { ssr: false });

// Renders a Vega-Lite spec at container width with theme-aware axes and legend.
export function Chart({ spec, data }: { spec: VegaSpec; data: readonly unknown[] }) {
  const dark = useIsDark();
  const full = useMemo<VisualizationSpec>(
    () =>
      ({
        $schema: "https://vega.github.io/schema/vega-lite/v5.json",
        ...spec,
        width: "container",
        data: { values: data as unknown[] },
      }) as VisualizationSpec,
    [spec, data],
  );
  const options = useMemo<EmbedOptions>(
    () => ({
      actions: false,
      renderer: "svg",
      mode: "vega-lite",
      config: {
        background: "transparent",
        font: "inherit",
        axis: {
          labelColor: dark ? "#94a3b8" : "#475569",
          titleColor: dark ? "#e2e8f0" : "#1e293b",
          gridColor: dark ? "#30363d" : "#e2e8f0",
          domainColor: dark ? "#30363d" : "#cbd5e1",
          tickColor: dark ? "#30363d" : "#cbd5e1",
        },
        legend: {
          labelColor: dark ? "#e2e8f0" : "#1e293b",
          titleColor: dark ? "#e2e8f0" : "#1e293b",
        },
        view: { stroke: null },
      },
    }),
    [dark],
  );
  return <VegaEmbed spec={full} options={options} className="w-full [&_.vega-embed]:w-full" />;
}

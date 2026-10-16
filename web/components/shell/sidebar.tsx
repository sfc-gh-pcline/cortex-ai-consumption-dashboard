"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Cable,
  Code,
  DatabaseZap,
  Donut,
  FunctionSquare,
  Gauge,
  Search,
  Settings,
  Terminal,
  Users,
  Bot,
  type LucideIcon,
} from "lucide-react";

import { ThemeToggle } from "@/components/shell/theme-toggle";
import { BUILD_INFO, buildCaption, fingerprintCaption } from "@/lib/build-info";
import { useDashboard } from "@/lib/client/dashboard";
import { PAGES, urlPath } from "@/lib/view-state";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  total_consumption: Donut,
  coco: Terminal,
  cowork: Users,
  cortex_agents: Bot,
  cortex_search: Search,
  cortex_inference_api: Cable,
  cortex_ai_functions: FunctionSquare,
  warehouse_attribution: DatabaseZap,
  sql: Code,
  acceleration_sql: Gauge,
  config: Settings,
};

// Grouped navigation from PAGES. The current query string travels with every
// link so the window and filters survive page changes, as bound URL state did.
export function Sidebar() {
  const { page, preflight } = useDashboard();
  const search = useSearchParams().toString();
  const groups = [...new Set(PAGES.map((p) => p.group))];
  return (
    <aside className="flex w-60 shrink-0 flex-col gap-4 bg-sidebar px-3 py-4 text-sidebar-foreground">
      <div className="px-2">
        <div className="text-xs uppercase tracking-wide text-sidebar-foreground/60">Snowflake</div>
        <div className="text-sm font-semibold">Cortex AI Consumption Dashboard</div>
      </div>
      <nav className="flex flex-1 flex-col gap-3" aria-label="Pages">
        {groups.map((group) => (
          <div key={group || "overview"}>
            {group && (
              <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-sidebar-foreground/60">
                {group}
              </div>
            )}
            <ul className="flex flex-col gap-0.5">
              {PAGES.filter((p) => p.group === group).map((item) => {
                const Icon = ICONS[item.pageId] ?? Donut;
                const active = item.pageId === page.pageId;
                const href = `/${urlPath(item)}${search ? `?${search}` : ""}`;
                return (
                  <li key={item.pageId}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent",
                        active && "bg-sidebar-accent font-medium text-sidebar-accent-foreground",
                      )}
                    >
                      <Icon className="size-4 shrink-0 text-sidebar-primary" aria-hidden />
                      {item.title}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="flex items-center justify-between px-2 text-xs text-sidebar-foreground/60">
        <details className="min-w-0">
          <summary
            className="cursor-pointer list-none truncate"
            title={`${fingerprintCaption()}. Click for component versions.`}
          >
            {buildCaption()}
            {preflight?.viewer ? ` · ${preflight.viewer}` : ""}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 pl-1" aria-label="Component versions">
            <li>Build {BUILD_INFO.projectVersion}</li>
            {BUILD_INFO.gitSha && <li>Commit {BUILD_INFO.gitSha}</li>}
            {BUILD_INFO.components.map((c) => (
              <li key={c.label}>
                {c.label} {c.version}
              </li>
            ))}
            {BUILD_INFO.node && <li>Node {BUILD_INFO.node}</li>}
          </ul>
        </details>
        <ThemeToggle />
      </div>
    </aside>
  );
}

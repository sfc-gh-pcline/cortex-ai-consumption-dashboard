"use client";

import { FilterX, RefreshCw, RotateCcw } from "lucide-react";
import { useQueryStates, parseAsString, parseAsBoolean } from "nuqs";
import { useState } from "react";

import { MultiSelect } from "@/components/kit/multi-select";
import { Caption } from "@/components/kit/notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDashboard } from "@/lib/client/dashboard";
import type { PageFacts } from "@/lib/client/page-data";
import { PAGE_BREAKDOWN_DEFAULTS, PAGE_FILTER_KEYS, stringList } from "@/lib/client/url-state";
import { DEFAULT_PRESET } from "@/lib/dates";

// Global user / model / tag filters plus the view actions. Unsupported
// dimensions stay visible but disabled with their carried selections intact.
export function FilterBar({ facts }: { facts: PageFacts }) {
  const { page, filters, setFilters, preflight } = useDashboard();
  const pageKeys = PAGE_FILTER_KEYS[page.pageId] ?? [];
  const [, setPageValues] = useQueryStates(
    Object.fromEntries(pageKeys.map((key) => [key, stringList])),
  );
  const [, setView] = useQueryStates({
    range: parseAsString,
    start: parseAsString,
    end: parseAsString,
    [`trend_${page.pageId}`]: parseAsString,
    [`compare_${page.pageId}`]: parseAsBoolean,
    ...(PAGE_BREAKDOWN_DEFAULTS[page.pageId]
      ? { [PAGE_BREAKDOWN_DEFAULTS[page.pageId][0]]: parseAsString }
      : {}),
    [`detail_warehouse_${page.dependencyKey}`]: parseAsBoolean,
    detail_search: parseAsBoolean,
    detail_cowork_mix: parseAsBoolean,
    overview_service: parseAsString,
    overview_dimension: parseAsString,
  });
  const [reloading, setReloading] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const active = filters.users.length + filters.models.length + filters.tags.length;
  const userSupported = facts.capabilities.has("USER_NAME");
  const modelSupported = facts.capabilities.has("MODEL_NAME");

  const clearFilters = () => {
    setFilters({ users: [], models: [], tags: [] });
    void setPageValues(Object.fromEntries(pageKeys.map((key) => [key, null])));
  };
  const resetView = () => {
    clearFilters();
    void setView({
      range: DEFAULT_PRESET,
      start: null,
      end: null,
      [`trend_${page.pageId}`]: null,
      [`compare_${page.pageId}`]: null,
      [`detail_warehouse_${page.dependencyKey}`]: null,
      detail_search: null,
      detail_cowork_mix: null,
      overview_service: null,
      overview_dimension: null,
      ...(PAGE_BREAKDOWN_DEFAULTS[page.pageId]
        ? { [PAGE_BREAKDOWN_DEFAULTS[page.pageId][0]]: null }
        : {}),
    });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        {active > 0 && <Badge variant="secondary">{active} active</Badge>}
        <MultiSelect
          label="User"
          options={facts.userOptions}
          value={filters.users}
          onChange={(users) => setFilters({ users })}
          disabled={!userSupported}
          placeholder={`All users (${facts.userOptions.length})`}
        />
        <MultiSelect
          label="Model"
          options={facts.modelOptions}
          value={filters.models}
          onChange={(models) => setFilters({ models })}
          disabled={!modelSupported}
          placeholder={`All models (${facts.modelOptions.length})`}
        />
        <MultiSelect
          label="User tag"
          options={facts.tagOptions}
          value={filters.tags}
          onChange={(tags) => setFilters({ tags })}
          disabled={!userSupported}
          placeholder={`All tags (${facts.tagOptions.length})`}
          help="Resolved from current USER-domain tag references."
        />
        <Button variant="outline" size="sm" onClick={clearFilters}>
          <FilterX data-icon="inline-start" aria-hidden /> Clear filters on this view
        </Button>
        <Button variant="outline" size="sm" onClick={resetView}>
          <RotateCcw data-icon="inline-start" aria-hidden /> Reset view
        </Button>
        <Button
          size="sm"
          disabled={reloading}
          title={
            preflight?.isAdmin
              ? "Bypass this view's cached source results. Does not refresh aggregate tables or source ingestion."
              : "Refetch this view; recent results may still come from the server cache."
          }
          onClick={async () => {
            setReloading(true);
            setReloadError(null);
            try {
              await facts.reload();
            } catch (error) {
              setReloadError(error instanceof Error ? error.message : "Unable to refetch results.");
            } finally {
              setReloading(false);
            }
          }}
        >
          <RefreshCw
            data-icon="inline-start"
            aria-hidden
            className={reloading ? "animate-spin" : undefined}
          />{" "}
          {preflight?.isAdmin ? "Reload results" : "Refetch cached results"}
        </Button>
      </div>
      {reloadError && <Caption>{reloadError}</Caption>}
      <FilterSummary users={filters.users} models={filters.models} tags={filters.tags} />
    </div>
  );
}

function chip(label: string, values: readonly string[], limit = 2): string | null {
  if (values.length === 0) return null;
  return values.length <= limit
    ? `${label}: ${values.join(", ")}`
    : `${label}: ${values.length} selected`;
}

// Echoes active filters above the page so a view is never silently narrowed.
export function FilterSummary({
  users,
  models,
  tags,
}: {
  users: string[];
  models: string[];
  tags: string[];
}) {
  const chips = [chip("Users", users), chip("Models", models), chip("Tags", tags)].filter(Boolean);
  if (chips.length === 0) return null;
  return <Caption>Filtered: {chips.join("  ·  ")}</Caption>;
}

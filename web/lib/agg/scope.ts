// Scope partitioning: every row lands in exactly one of matched, unscoped
// (not attributable), or excluded. Port of partition_scope / apply_filters in
// utils/agg.py, including "known mismatches precede unknown dimensions".
import { UNKNOWN } from "@/lib/sql/core";
import { FILTER_CAPABILITIES, type FilterSelection } from "@/lib/view-state";
import { hasColumn, isMissing, type Row } from "@/lib/agg/rows";

export interface ScopedFacts {
  readonly matched: Row[];
  readonly unscoped: Row[];
  readonly excluded: Row[];
}

export interface ScopeOptions {
  // Capability identity for dedicated frames, even when a SURFACE column exists.
  readonly source?: string | null;
  // Users carrying the selected tags: [] means the tags matched nobody, null means inactive.
  readonly tagUsers?: readonly string[] | null;
}

function supportsColumn(surface: string | undefined, column: string): boolean {
  return surface !== undefined && (FILTER_CAPABILITIES[surface]?.has(column) ?? false);
}

export function partitionScope(
  rows: readonly Row[] | null | undefined,
  selection: FilterSelection,
  options: ScopeOptions = {},
): ScopedFacts {
  const frame = rows ?? [];
  const source = options.source ?? null;
  const tagUsers = options.tagUsers ?? null;
  const unknown = new Array<boolean>(frame.length).fill(false);
  const excluded = new Array<boolean>(frame.length).fill(false);

  const predicates: [string, readonly string[]][] = [
    ["USER_NAME", selection.users],
    ["MODEL_NAME", selection.models],
    ...Object.entries(selection.pageValues),
  ];
  if (tagUsers !== null) {
    predicates.push(["USER_NAME", tagUsers]);
  } else if (selection.tags.length > 0) {
    unknown.fill(true);
  }

  const hasSurface = hasColumn(frame, "SURFACE");
  predicates.forEach(([column, values], position) => {
    const isTagPredicate = tagUsers !== null && position === predicates.length - 1;
    if (values.length === 0 && !isTagPredicate) return;
    if (!hasColumn(frame, column)) {
      unknown.fill(true);
      return;
    }
    const wanted = new Set(values);
    frame.forEach((row, index) => {
      let supported: boolean;
      if (source !== null) supported = supportsColumn(source, column);
      else if (hasSurface) supported = supportsColumn(String(row.SURFACE), column);
      else supported = true;
      const value = row[column];
      const known = supported && !isMissing(value) && value !== UNKNOWN && value !== "";
      if (known && !wanted.has(String(value))) excluded[index] = true;
      if (!known) unknown[index] = true;
    });
  });

  return {
    matched: frame.filter((_, i) => !excluded[i] && !unknown[i]),
    unscoped: frame.filter((_, i) => !excluded[i] && unknown[i]),
    excluded: frame.filter((_, i) => excluded[i]),
  };
}

// Convenience wrapper mirroring apply_filters.
export function applyFilters(
  rows: readonly Row[] | null | undefined,
  filters: {
    users?: readonly string[];
    models?: readonly string[];
    tagUsers?: readonly string[] | null;
    source?: string | null;
    pageValues?: Readonly<Record<string, readonly string[]>>;
  } = {},
): ScopedFacts {
  return partitionScope(
    rows,
    {
      users: filters.users ?? [],
      models: filters.models ?? [],
      tags: [],
      pageValues: filters.pageValues ?? {},
    },
    { source: filters.source ?? null, tagUsers: filters.tagUsers ?? null },
  );
}

// Sorted distinct `name = value` labels available for filtering.
export function tagLabelOptions(tags: readonly Row[] | null | undefined): string[] {
  if (!tags || tags.length === 0) return [];
  return [
    ...new Set(tags.filter((t) => !isMissing(t.TAG_LABEL)).map((t) => String(t.TAG_LABEL))),
  ].sort();
}

// Usernames carrying any of the selected labels; empty for no selection.
export function usersForTags(
  tags: readonly Row[] | null | undefined,
  selected: readonly string[] | null | undefined,
): string[] {
  if (!tags || tags.length === 0 || !selected || selected.length === 0) return [];
  const wanted = new Set(selected);
  return [
    ...new Set(
      tags
        .filter((t) => wanted.has(String(t.TAG_LABEL)) && !isMissing(t.USER_NAME))
        .map((t) => String(t.USER_NAME)),
    ),
  ].sort();
}

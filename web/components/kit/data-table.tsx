"use client";

import {
  columnVisibilityFeature,
  createSortedRowModel,
  flexRender,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Row } from "@/lib/agg/rows";
import { cn } from "@/lib/utils";

export interface ColumnSpec {
  readonly key: string;
  readonly label: string;
  // Renders the cell; defaults to the value's string form ("" for missing).
  readonly render?: (value: unknown, row: Row) => ReactNode;
  readonly align?: "left" | "right";
  readonly sortable?: boolean;
}

export interface DataTableProps {
  readonly rows: readonly Row[];
  readonly columns: readonly ColumnSpec[];
  readonly initialSort?: { key: string; desc: boolean };
  readonly caption?: string;
  readonly maxHeight?: number;
}

const textOf = (value: unknown) => (value === null || value === undefined ? "" : String(value));

const features = tableFeatures({
  columnVisibilityFeature,
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});

// Sortable table over row objects, the equivalent of st.dataframe with column_config.
export function DataTable({
  rows,
  columns,
  initialSort,
  caption,
  maxHeight = 480,
}: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>(
    initialSort ? [{ id: initialSort.key, desc: initialSort.desc }] : [],
  );
  const defs = useMemo<ColumnDef<typeof features, Row>[]>(
    () =>
      columns.map((column) => ({
        id: column.key,
        accessorFn: (row) => row[column.key],
        header: column.label,
        cell: (info) =>
          column.render
            ? column.render(info.getValue(), info.row.original)
            : textOf(info.getValue()),
        enableSorting: column.sortable ?? true,
        sortFn: (a, b, id) => {
          const x = a.getValue<unknown>(id);
          const y = b.getValue<unknown>(id);
          if (typeof x === "number" && typeof y === "number") return x - y;
          if (x === null || x === undefined) return -1;
          if (y === null || y === undefined) return 1;
          return String(x).localeCompare(String(y));
        },
      })),
    [columns],
  );
  const table = useTable({
    features,
    data: rows as Row[],
    columns: defs,
    state: { sorting },
    onSortingChange: setSorting,
  });
  return (
    <div className="flex flex-col gap-1">
      <div className="overflow-auto rounded-lg border" style={{ maxHeight }}>
        <Table>
          <TableHeader className="sticky top-0 bg-card">
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => {
                  const column = columns.find((c) => c.key === header.id);
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead
                      key={header.id}
                      className={cn("whitespace-nowrap", column?.align === "right" && "text-right")}
                    >
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1"
                        aria-label={`Sort by ${column?.label ?? header.id}`}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sorted === "asc" ? (
                          <ArrowUp className="size-3" aria-hidden />
                        ) : sorted === "desc" ? (
                          <ArrowDown className="size-3" aria-hidden />
                        ) : (
                          <ArrowUpDown className="size-3 opacity-40" aria-hidden />
                        )}
                      </button>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const column = columns.find((c) => c.key === cell.column.id);
                  return (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        "whitespace-nowrap tabular-nums",
                        column?.align === "right" && "text-right",
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
    </div>
  );
}

// CSV download of the visible rows, the st.dataframe toolbar's export.
export function toCsv(rows: readonly Row[], columns: readonly ColumnSpec[]): string {
  const escape = (value: unknown) => {
    const text = textOf(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const header = columns.map((c) => escape(c.label)).join(",");
  const body = rows.map((row) => columns.map((c) => escape(row[c.key])).join(","));
  return [header, ...body].join("\n");
}

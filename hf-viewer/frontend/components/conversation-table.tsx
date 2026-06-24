"use client";

import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import type { DatasetMeta } from "@/lib/types";

interface ConversationTableProps {
  meta: DatasetMeta;
  rows: { idx: number; row: Record<string, unknown> }[];
  total: number;
  offset: number;
  pageSize: number;
  onPageChange: (offset: number) => void;
  onOpen: (idx: number) => void;
}

function display(value: unknown): string {
  if (value === null || value === undefined) return "\u2014";
  if (typeof value === "boolean") return value ? "yes" : "no";
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > 80 ? s.slice(0, 80) + "\u2026" : s;
}

export function ConversationTable({
  meta,
  rows,
  total,
  offset,
  pageSize,
  onPageChange,
  onOpen,
}: ConversationTableProps) {
  const scalarCols = meta.columns.filter(
    (c) => !meta.message_columns.includes(c)
  );
  const page = Math.floor(offset / pageSize);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr className="border-b text-left">
              <th className="px-3 py-2 font-medium text-muted-foreground">#</th>
              {scalarCols.map((c) => (
                <th
                  key={c}
                  className="whitespace-nowrap px-3 py-2 font-medium text-muted-foreground"
                >
                  {c}
                </th>
              ))}
              {meta.message_columns.map((c) => (
                <th
                  key={c}
                  className="whitespace-nowrap px-3 py-2 text-center font-medium text-muted-foreground"
                >
                  {c} (turns)
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ idx, row }) => (
              <tr
                key={idx}
                onClick={() => onOpen(idx)}
                className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
              >
                <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                  {idx}
                </td>
                {scalarCols.map((c) => (
                  <td key={c} className="px-3 py-2">
                    {display(row[c])}
                  </td>
                ))}
                {meta.message_columns.map((c) => (
                  <td key={c} className="px-3 py-2 text-center font-mono text-xs">
                    {display(row[`_${c}_turns`])}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={scalarCols.length + meta.message_columns.length + 1}
                  className="px-3 py-8 text-center text-muted-foreground"
                >
                  No rows.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {total.toLocaleString()} rows &middot; page {page + 1} of {totalPages}
        </span>
        <div className="flex items-center gap-1">
          <PageButton disabled={page === 0} onClick={() => onPageChange(0)}>
            <ChevronsLeft className="size-3.5" />
          </PageButton>
          <PageButton
            disabled={page === 0}
            onClick={() => onPageChange(Math.max(0, offset - pageSize))}
          >
            <ChevronLeft className="size-3.5" />
          </PageButton>
          <PageButton
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange(offset + pageSize)}
          >
            <ChevronRight className="size-3.5" />
          </PageButton>
          <PageButton
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange((totalPages - 1) * pageSize)}
          >
            <ChevronsRight className="size-3.5" />
          </PageButton>
        </div>
      </div>
    </div>
  );
}

function PageButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-8 items-center justify-center rounded-md border border-border disabled:opacity-40 enabled:hover:bg-accent"
    >
      {children}
    </button>
  );
}

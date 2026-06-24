"use client";

import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Trash2,
} from "lucide-react";
import type { DatasetMeta } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ConversationTableProps {
  meta: DatasetMeta;
  rows: { idx: number; row: Record<string, unknown> }[];
  total: number;
  offset: number;
  pageSize: number;
  onPageChange: (offset: number) => void;
  onOpen: (idx: number) => void;
  /** When provided, a delete action is shown for each row (local mode only). */
  onDelete?: (sessionId: string) => void;
}

const LANG_LABELS: Record<string, string> = {
  mr: "Marathi",
  hi: "Hindi",
  en: "English",
  bhb: "Bhili",
};

type Tone = "muted" | "green" | "amber" | "red" | "blue" | "outline";

const TONES: Record<Tone, string> = {
  muted: "bg-secondary text-secondary-foreground",
  green:
    "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  amber:
    "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  red: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  blue: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  outline: "border border-border text-foreground",
};

function Pill({
  tone = "muted",
  children,
}: {
  tone?: Tone;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex w-fit items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium",
        TONES[tone]
      )}
    >
      {children}
    </span>
  );
}

function shorten(value: unknown, max = 48): string {
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > max ? s.slice(0, max) + "\u2026" : s;
}

function Cell({ col, value }: { col: string; value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">{"\u2014"}</span>;
  }

  switch (col) {
    case "completed": {
      const done = value === true || value === "true";
      return <Pill tone={done ? "green" : "amber"}>{done ? "Completed" : "Incomplete"}</Pill>;
    }
    case "scenario_category":
    case "category":
      return <Pill tone="muted">{String(value)}</Pill>;
    case "target_language":
    case "language":
      return <Pill tone="outline">{LANG_LABELS[String(value)] ?? String(value)}</Pill>;
    case "agent_type":
      return <Pill tone="blue">{String(value)}</Pill>;
    case "scenario_id":
      return <span className="font-mono text-xs">{String(value)}</span>;
    case "session_id":
      return (
        <span className="font-mono text-xs text-muted-foreground">
          {shorten(value, 14)}
        </span>
      );
  }

  if (typeof value === "boolean") {
    return <Pill tone={value ? "green" : "muted"}>{value ? "yes" : "no"}</Pill>;
  }
  return (
    <span className="block max-w-[260px] truncate" title={String(value)}>
      {shorten(value)}
    </span>
  );
}

export function ConversationTable({
  meta,
  rows,
  total,
  offset,
  pageSize,
  onPageChange,
  onOpen,
  onDelete,
}: ConversationTableProps) {
  const scalarCols = meta.columns.filter(
    (c) => !meta.message_columns.includes(c)
  );
  const page = Math.floor(offset / pageSize);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full caption-bottom text-sm">
          <thead>
            <tr className="border-b">
              <th className="h-10 px-3 text-left align-middle font-medium text-muted-foreground">
                #
              </th>
              {scalarCols.map((c) => (
                <th
                  key={c}
                  className="h-10 whitespace-nowrap px-3 text-left align-middle font-medium text-muted-foreground"
                >
                  {c}
                </th>
              ))}
              {meta.message_columns.map((c) => (
                <th
                  key={c}
                  className="h-10 whitespace-nowrap px-3 text-center align-middle font-medium text-muted-foreground"
                >
                  {c}
                </th>
              ))}
              {onDelete && <th className="h-10 w-10 px-3" />}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ idx, row }) => (
              <tr
                key={idx}
                onClick={() => onOpen(idx)}
                className="cursor-pointer border-b transition-colors last:border-0 hover:bg-muted/50"
              >
                <td className="px-3 py-2 align-middle font-mono text-xs text-muted-foreground">
                  {idx}
                </td>
                {scalarCols.map((c) => (
                  <td key={c} className="px-3 py-2 align-middle">
                    <Cell col={c} value={row[c]} />
                  </td>
                ))}
                {meta.message_columns.map((c) => (
                  <td
                    key={c}
                    className="px-3 py-2 text-center align-middle font-mono text-xs tabular-nums text-muted-foreground"
                  >
                    {row[`_${c}_turns`] == null ? "\u2014" : String(row[`_${c}_turns`])}
                  </td>
                ))}
                {onDelete && (
                  <td className="px-3 py-2 text-right align-middle">
                    <button
                      type="button"
                      title="Delete conversation (local only)"
                      disabled={!row.session_id}
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(String(row.session_id ?? ""));
                      }}
                      className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors disabled:opacity-30 enabled:hover:bg-red-100 enabled:hover:text-red-700 dark:enabled:hover:bg-red-950 dark:enabled:hover:text-red-300"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={
                    scalarCols.length +
                    meta.message_columns.length +
                    1 +
                    (onDelete ? 1 : 0)
                  }
                  className="px-3 py-10 text-center text-muted-foreground"
                >
                  No rows.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between px-0.5">
        <span className="text-xs text-muted-foreground tabular-nums">
          {total.toLocaleString()} rows &middot; page {page + 1} of {totalPages}
        </span>
        <div className="flex items-center gap-1">
          <PageButton disabled={page === 0} onClick={() => onPageChange(0)}>
            <ChevronsLeft className="size-4" />
          </PageButton>
          <PageButton
            disabled={page === 0}
            onClick={() => onPageChange(Math.max(0, offset - pageSize))}
          >
            <ChevronLeft className="size-4" />
          </PageButton>
          <PageButton
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange(offset + pageSize)}
          >
            <ChevronRight className="size-4" />
          </PageButton>
          <PageButton
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange((totalPages - 1) * pageSize)}
          >
            <ChevronsRight className="size-4" />
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
      className="inline-flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground disabled:opacity-30 enabled:hover:bg-accent enabled:hover:text-foreground"
    >
      {children}
    </button>
  );
}

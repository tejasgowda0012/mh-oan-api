"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ChevronRight, Loader2 } from "lucide-react";
import { apiFetch, buildQuery } from "@/lib/api";
import type { RowResponse } from "@/lib/types";
import { parseConversation } from "@/lib/parse";
import { Timeline } from "@/components/timeline";
import { ThemeToggle } from "@/components/theme-toggle";

export default function ConversationPage({
  params,
}: {
  params: Promise<{ idx: string }>;
}) {
  const { idx } = use(params);
  const search = useSearchParams();
  const dataset = search.get("dataset") ?? "";
  const config = search.get("config");
  const split = search.get("split") ?? "train";

  const [data, setData] = useState<RowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCol, setActiveCol] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    setError(null);
    const qs = buildQuery({ dataset, config, split, idx });
    apiFetch<RowResponse>(`/api/row?${qs}`)
      .then((d) => {
        setData(d);
        setActiveCol(d.message_columns[0] ?? "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [dataset, config, split, idx]);

  const backHref = `/?${buildQuery({ dataset, config, split })}`;

  const scalarEntries = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.row).filter(
      ([k]) => !data.message_columns.includes(k)
    );
  }, [data]);

  const timeline = useMemo(() => {
    if (!data || !activeCol) return [];
    return parseConversation(data.row[activeCol]);
  }, [data, activeCol]);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-5 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <Link
              href={backHref}
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-md hover:bg-accent"
            >
              <ArrowLeft className="size-4" />
            </Link>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold leading-tight tracking-tight">
                Conversation #{idx}
              </h1>
              <p className="truncate font-mono text-[11px] text-muted-foreground">
                {dataset}
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-4 px-5 py-5">
        {loading && (
          <div className="flex justify-center py-12">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}

        {data && !loading && (
          <>
            {scalarEntries.length > 0 && (
              <details className="group rounded-md border bg-muted/20">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  <ChevronRight className="size-3 transition-transform group-open:rotate-90" />
                  Metadata
                  <span className="font-normal lowercase opacity-60">
                    ({scalarEntries.length} fields)
                  </span>
                </summary>
                <div className="flex flex-wrap gap-1.5 px-3 pb-3">
                  {scalarEntries.map(([k, v]) => (
                    <span
                      key={k}
                      className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-background px-2 py-0.5 text-[11px]"
                      title={stringify(v)}
                    >
                      <span className="text-muted-foreground">{k}</span>
                      <span className="max-w-[200px] truncate font-medium">
                        {stringify(v)}
                      </span>
                    </span>
                  ))}
                </div>
              </details>
            )}

            {data.message_columns.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No conversation column detected in this dataset.
              </p>
            ) : (
              <div className="space-y-3">
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Transcript
                </div>
                {data.message_columns.length > 1 && (
                  <div className="flex flex-wrap gap-1.5">
                    {data.message_columns.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setActiveCol(c)}
                        className={
                          "rounded-md border px-2.5 py-1 text-[11px] font-medium " +
                          (activeCol === c
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border hover:bg-accent")
                        }
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                )}
                <Timeline entries={timeline} />
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function stringify(v: unknown): string {
  if (v === null || v === undefined) return "\u2014";
  if (typeof v === "string") return v;
  if (typeof v === "boolean") return v ? "yes" : "no";
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

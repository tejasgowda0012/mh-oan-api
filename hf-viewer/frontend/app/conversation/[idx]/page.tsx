"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
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
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <Link
              href={backHref}
              className="inline-flex size-9 items-center justify-center rounded-md hover:bg-accent"
            >
              <ArrowLeft className="size-4" />
            </Link>
            <div>
              <h1 className="text-lg font-semibold">Conversation #{idx}</h1>
              <p className="font-mono text-xs text-muted-foreground">{dataset}</p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-6">
        {loading && (
          <div className="flex justify-center py-12">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {data && !loading && (
          <>
            {scalarEntries.length > 0 && (
              <div className="rounded-md border p-4">
                <h2 className="mb-3 text-sm font-semibold">Metadata</h2>
                <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
                  {scalarEntries.map(([k, v]) => (
                    <div key={k} className="min-w-0">
                      <dt className="text-xs text-muted-foreground">{k}</dt>
                      <dd className="truncate text-sm" title={stringify(v)}>
                        {stringify(v)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}

            {data.message_columns.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No conversation column detected in this dataset.
              </p>
            ) : (
              <div className="space-y-4">
                {data.message_columns.length > 1 && (
                  <div className="flex flex-wrap gap-2">
                    {data.message_columns.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setActiveCol(c)}
                        className={
                          "rounded-md border px-3 py-1.5 text-xs font-medium " +
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

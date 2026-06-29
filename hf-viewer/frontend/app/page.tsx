"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { apiDelete, apiFetch, buildQuery } from "@/lib/api";
import type { DatasetMeta, RowsResponse } from "@/lib/types";
import { DatasetLoader } from "@/components/dataset-loader";
import { ConversationTable } from "@/components/conversation-table";
import { SessionIdSearch } from "@/components/session-id-search";
import { ThemeToggle } from "@/components/theme-toggle";

const DEFAULT_DATASET = "kenpath/mh-synthetic-v1";
const PAGE_SIZE = 50;
const STORE_KEY = "hf_view";

interface ViewState {
  dataset: string;
  config: string | null;
  split: string;
  offset: number;
}

export default function HomePage() {
  const router = useRouter();
  const [meta, setMeta] = useState<DatasetMeta | null>(null);
  const [rows, setRows] = useState<RowsResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRows = useCallback(
    async (m: DatasetMeta, newOffset: number) => {
      setRowsLoading(true);
      setError(null);
      try {
        const qs = buildQuery({
          dataset: m.dataset,
          config: m.config,
          split: m.split,
          offset: newOffset,
          length: PAGE_SIZE,
        });
        const data = await apiFetch<RowsResponse>(`/api/rows?${qs}`);
        setRows(data);
        setOffset(newOffset);
        sessionStorage.setItem(
          STORE_KEY,
          JSON.stringify({
            dataset: m.dataset,
            config: m.config,
            split: m.split,
            offset: newOffset,
          } satisfies ViewState)
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setRowsLoading(false);
      }
    },
    []
  );

  const loadDataset = useCallback(
    async (dataset: string, config?: string | null, split?: string | null, startOffset = 0) => {
      setLoading(true);
      setError(null);
      setRows(null);
      try {
        const qs = buildQuery({ dataset, config, split });
        const m = await apiFetch<DatasetMeta>(`/api/load?${qs}`);
        setMeta(m);
        await fetchRows(m, startOffset);
      } catch (e) {
        setMeta(null);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [fetchRows]
  );

  // Restore last view on mount.
  useEffect(() => {
    const raw = sessionStorage.getItem(STORE_KEY);
    if (raw) {
      try {
        const s = JSON.parse(raw) as ViewState;
        loadDataset(s.dataset, s.config, s.split, s.offset);
      } catch {
        // ignore
      }
    }
  }, [loadDataset]);

  const openConversation = (idx: number) => {
    if (!meta) return;
    const qs = buildQuery({
      dataset: meta.dataset,
      config: meta.config,
      split: meta.split,
    });
    router.push(`/conversation/${idx}?${qs}`);
  };

  const onConfigSplitChange = (config: string, split: string) => {
    if (!meta) return;
    loadDataset(meta.dataset, config, split, 0);
  };

  const handleDelete = useCallback(
    async (sessionId: string) => {
      if (!meta || !sessionId) return;
      if (
        !window.confirm(
          "Delete this conversation from the local JSONL? This cannot be undone " +
            "(the Hugging Face dataset is not affected)."
        )
      ) {
        return;
      }
      try {
        await apiDelete(
          `/api/row?${buildQuery({ dataset: meta.dataset, session_id: sessionId })}`
        );
        const newTotal = Math.max(0, (rows?.num_rows_total ?? 1) - 1);
        // If we just emptied the last page, step back one page.
        const target =
          offset >= newTotal && offset >= PAGE_SIZE ? offset - PAGE_SIZE : offset;
        setMeta((m) => (m ? { ...m, num_rows: Math.max(0, m.num_rows - 1) } : m));
        await fetchRows(meta, target);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [meta, rows, offset, fetchRows]
  );

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1280px] items-center justify-between gap-4 px-5 py-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">
              HF Conversation Viewer
            </h1>
            <p className="text-xs text-muted-foreground">
              Browse Hugging Face conversation datasets with tool calls
            </p>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] space-y-4 px-5 py-5">
        <DatasetLoader
          initialDataset={meta?.dataset ?? DEFAULT_DATASET}
          loading={loading}
          onLoad={(d) => loadDataset(d)}
        />

        {error && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}

        {meta && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
              <span className="font-mono font-medium text-foreground">
                {meta.dataset}
              </span>

              {meta.source === "local" ? (
                <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-800 dark:bg-green-950 dark:text-green-300">
                  local copy &middot; editable
                </span>
              ) : (
                <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[11px] font-medium">
                  hub &middot; read-only
                </span>
              )}

              {meta.configs.length > 1 && (
                <label className="flex items-center gap-1.5">
                  <span>config</span>
                  <select
                    value={meta.config ?? ""}
                    onChange={(e) =>
                      onConfigSplitChange(e.target.value, meta.split)
                    }
                    className="h-7 rounded-md border border-input bg-transparent px-2 text-xs"
                  >
                    {meta.configs.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {meta.splits.length > 1 && (
                <label className="flex items-center gap-1.5">
                  <span>split</span>
                  <select
                    value={meta.split}
                    onChange={(e) =>
                      onConfigSplitChange(meta.config ?? "", e.target.value)
                    }
                    className="h-7 rounded-md border border-input bg-transparent px-2 text-xs"
                  >
                    {meta.splits.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <span className="tabular-nums">
                {meta.num_rows.toLocaleString()} rows
              </span>
              {meta.message_columns.length > 0 && (
                <span>
                  conversation:{" "}
                  <span className="font-mono text-foreground">
                    {meta.message_columns.join(", ")}
                  </span>
                </span>
              )}
            </div>

            <SessionIdSearch meta={meta} onOpen={openConversation} />

            {rowsLoading && !rows ? (
              <div className="flex justify-center py-12">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : rows ? (
              <ConversationTable
                meta={meta}
                rows={rows.rows}
                total={rows.num_rows_total}
                offset={offset}
                pageSize={PAGE_SIZE}
                onPageChange={(o) => fetchRows(meta, o)}
                onOpen={openConversation}
                onDelete={meta.source === "local" ? handleDelete : undefined}
              />
            ) : null}
          </div>
        )}

        {!meta && !loading && !error && (
          <p className="text-xs text-muted-foreground">
            Enter a dataset id (default{" "}
            <span className="font-mono">{DEFAULT_DATASET}</span>) and click{" "}
            <span className="font-medium text-foreground">Load dataset</span>.
            Private datasets need an HF token.
          </p>
        )}
      </main>
    </div>
  );
}

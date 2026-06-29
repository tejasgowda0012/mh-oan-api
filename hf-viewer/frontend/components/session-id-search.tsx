"use client";

import { useState } from "react";
import { Loader2, Search, X } from "lucide-react";
import { apiFetch, buildQuery } from "@/lib/api";
import type { DatasetMeta, FindResponse } from "@/lib/types";

interface SessionIdSearchProps {
  meta: DatasetMeta;
  onOpen: (idx: number) => void;
}

export function SessionIdSearch({ meta, onOpen }: SessionIdSearchProps) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<FindResponse | null>(null);

  const clear = () => {
    setQuery("");
    setResults(null);
    setError(null);
  };

  const search = async () => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const qs = buildQuery({
        dataset: meta.dataset,
        config: meta.config,
        split: meta.split,
        session_id: trimmed,
      });
      const data = await apiFetch<FindResponse>(`/api/find?${qs}`);

      if (data.num_matches === 0) {
        setError(`No session found matching "${trimmed}"`);
        return;
      }

      if (data.num_matches === 1) {
        onOpen(data.matches[0].idx);
        return;
      }

      setResults(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1 max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") search();
            }}
            placeholder="Search session ID…"
            className="h-9 w-full rounded-md border border-input bg-transparent pl-9 pr-8 font-mono text-xs outline-none ring-offset-background placeholder:font-sans placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          />
          {query && (
            <button
              type="button"
              onClick={clear}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={search}
          disabled={loading || !query.trim()}
          className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Search className="size-3.5" />
          )}
          Search
        </button>
      </div>

      {error && (
        <p className="text-xs text-destructive">{error}</p>
      )}

      {results && results.num_matches > 0 && (
        <div className="rounded-lg border bg-muted/30">
          <p className="border-b px-3 py-2 text-xs text-muted-foreground">
            {results.num_matches} match{results.num_matches === 1 ? "" : "es"}
            {results.truncated ? " (showing first 25)" : ""}
          </p>
          <ul className="max-h-48 divide-y overflow-y-auto">
            {results.matches.map(({ idx, row }) => (
              <li key={idx}>
                <button
                  type="button"
                  onClick={() => onOpen(idx)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs hover:bg-muted/60"
                >
                  <span className="font-mono">{String(row.session_id ?? "—")}</span>
                  <span className="shrink-0 text-muted-foreground">
                    #{idx}
                    {row.target_language ? ` · ${String(row.target_language)}` : ""}
                    {row.scenario_category ? ` · ${String(row.scenario_category)}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Database, KeyRound, Loader2 } from "lucide-react";
import { getToken, setToken } from "@/lib/api";

interface DatasetLoaderProps {
  initialDataset: string;
  loading: boolean;
  onLoad: (dataset: string) => void;
}

export function DatasetLoader({
  initialDataset,
  loading,
  onLoad,
}: DatasetLoaderProps) {
  const [dataset, setDataset] = useState(initialDataset);
  const [token, setTokenState] = useState("");
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    setTokenState(getToken());
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setToken(token.trim());
    onLoad(dataset.trim());
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Database className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            placeholder="owner/dataset  or  https://huggingface.co/datasets/owner/dataset"
            className="h-9 w-full rounded-md border border-input bg-transparent pl-8 pr-3 text-[13px] outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <button
          type="button"
          onClick={() => setShowToken((s) => !s)}
          title="HF token for private/gated datasets"
          className={
            "inline-flex h-9 items-center justify-center gap-1.5 rounded-md border px-3 text-xs " +
            (token
              ? "border-green-500/40 text-green-700 dark:text-green-400"
              : "border-input text-muted-foreground hover:bg-accent")
          }
        >
          <KeyRound className="size-3.5" />
          {token ? "Token set" : "Token"}
        </button>
        <button
          type="submit"
          disabled={loading || !dataset.trim()}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-[13px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {loading && <Loader2 className="size-4 animate-spin" />}
          Load
        </button>
      </div>

      {showToken && (
        <div className="space-y-1">
          <input
            value={token}
            onChange={(e) => setTokenState(e.target.value)}
            type="password"
            placeholder="hf_..."
            className="h-8 w-full max-w-md rounded-md border border-input bg-transparent px-2.5 text-[13px] outline-none focus:ring-2 focus:ring-ring"
          />
          <p className="text-[11px] text-muted-foreground">
            Stored in your browser only, sent with each request to the local
            backend.
          </p>
        </div>
      )}
    </form>
  );
}

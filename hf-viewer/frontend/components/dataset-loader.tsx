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
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Database className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            placeholder="owner/dataset  or  https://huggingface.co/datasets/owner/dataset"
            className="h-10 w-full rounded-md border border-input bg-transparent pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !dataset.trim()}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {loading && <Loader2 className="size-4 animate-spin" />}
          Load dataset
        </button>
      </div>

      <div>
        <button
          type="button"
          onClick={() => setShowToken((s) => !s)}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <KeyRound className="size-3.5" />
          {token ? "HF token set" : "Add HF token (for private/gated datasets)"}
        </button>
        {showToken && (
          <input
            value={token}
            onChange={(e) => setTokenState(e.target.value)}
            type="password"
            placeholder="hf_..."
            className="mt-2 h-9 w-full max-w-md rounded-md border border-input bg-transparent px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          The token is stored in your browser only and sent with each request to
          the local backend.
        </p>
      </div>
    </form>
  );
}

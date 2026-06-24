"""Export a Hugging Face dataset to a local JSONL working copy.

Once a dataset is exported here, the viewer reads/edits it locally and any
deletions are persisted only to this file -- the Hugging Face Hub is never
modified. When the data is finalized you can push it back to HF separately.

Usage (from the project root, reusing the repo venv)::

    ./.venv/bin/python hf-viewer/backend/export_jsonl.py kenpath/mh-synthetic-v1

Options::

    --config CONFIG   dataset config (default: first available)
    --split  SPLIT    split to export (default: train)
    --force           overwrite an existing local JSONL

A token is read from HF_TOKEN / HUGGING_FACE_HUB_TOKEN / HUGGINGFACE_WRITE_TOKEN
for private or gated datasets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from datasets import load_dataset

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def resolve_token() -> str | None:
    return (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_WRITE_TOKEN")
        or None
    )


def normalize_dataset(raw: str) -> str:
    """Accept a bare id (`owner/name`) or a full HF URL and return the id."""
    name = (raw or "").strip()
    marker = "huggingface.co/datasets/"
    if marker in name:
        name = name.split(marker, 1)[1]
    for sep in ("/tree/", "/blob/", "/viewer", "?", "#"):
        if sep in name:
            name = name.split(sep, 1)[0]
    return name.strip().strip("/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export an HF dataset to local JSONL.")
    ap.add_argument("dataset", help="HF dataset id or URL")
    ap.add_argument("--config", default=None, help="dataset config name")
    ap.add_argument("--split", default="train", help="split to export (default: train)")
    ap.add_argument("--force", action="store_true", help="overwrite existing JSONL")
    args = ap.parse_args()

    name = normalize_dataset(args.dataset)
    if not name:
        print("error: empty dataset name", file=sys.stderr)
        return 2

    out = DATA_DIR / f"{name.replace('/', '__')}.jsonl"
    if out.exists() and not args.force:
        print(
            f"Local JSONL already exists: {out}\n"
            "Use --force to overwrite (this discards local deletions).",
            file=sys.stderr,
        )
        return 1

    token = resolve_token()
    print(f"Loading {name} (config={args.config or 'default'}, split={args.split}) ...")
    ds = load_dataset(name, args.config, split=args.split, token=token)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in ds:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    tmp.replace(out)

    print(f"Wrote {len(ds)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

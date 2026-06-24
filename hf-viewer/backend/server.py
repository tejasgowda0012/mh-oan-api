"""FastAPI backend for the Hugging Face dataset conversation viewer.

Loads a dataset from the Hugging Face Hub (by id or URL) using the `datasets`
library and serves untruncated rows so the frontend can render full
conversations, including every tool call and tool return.

Run (reusing the repo venv). The folder name contains a hyphen, so it is not a
valid Python module path; run from inside this directory::

    cd hf-viewer/backend
    ../../.venv/bin/uvicorn server:app --port 8100 --reload

Or simply use the helper from the project root::

    ./hf-viewer/run.sh

Authentication: a Hugging Face token can be supplied per-request via the
``x-hf-token`` header (sent by the frontend) or via the ``HF_TOKEN`` /
``HUGGING_FACE_HUB_TOKEN`` environment variables. A token is required for
private or gated datasets such as ``kenpath/mh-synthetic-v1``.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from datasets import (
    get_dataset_config_names,
    get_dataset_split_names,
    load_dataset,
)
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="HF Dataset Conversation Viewer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache loaded datasets keyed by (name, config, split) so repeated browsing is
# instant. Loading a dataset can download files, so we guard with a lock.
_CACHE: dict[tuple, Any] = {}
_LOCK = threading.Lock()

# Column names that commonly hold a conversation / message history.
MESSAGE_COLUMN_CANDIDATES = [
    "messages",
    "conversation",
    "conversations",
    "chat",
    "dialogue",
    "agrinet_messages_json",
    "user_messages_json",
    "agrinet_messages_mr_json",
    "user_messages_mr_json",
]


def _resolve_token(x_hf_token: Optional[str]) -> Optional[str]:
    token = (x_hf_token or "").strip()
    return (
        token
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_WRITE_TOKEN")
        or None
    )


def _normalize_dataset(raw: str) -> str:
    """Accept a bare id (`owner/name`) or a full HF URL and return the id."""
    name = (raw or "").strip()
    marker = "huggingface.co/datasets/"
    if marker in name:
        name = name.split(marker, 1)[1]
    for sep in ("/tree/", "/blob/", "/viewer", "?", "#"):
        if sep in name:
            name = name.split(sep, 1)[0]
    return name.strip().strip("/")


def _try_parse_list(value: Any) -> Optional[list]:
    """Return a list of message objects if `value` looks like one, else None."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not (s.startswith("[") or s.startswith("{")):
            return None
        try:
            parsed = json.loads(s)
        except Exception:
            return None
        if isinstance(parsed, list):
            return parsed
    return None


def _looks_like_messages(parsed: list) -> bool:
    if not parsed or not isinstance(parsed[0], dict):
        return False
    keys = set(parsed[0].keys())
    return bool(keys & {"role", "parts", "content", "tool_calls"})


def _detect_message_columns(ds) -> list[str]:
    cols: list[str] = []
    sample = ds[0] if len(ds) > 0 else {}
    for col in ds.column_names:
        if col in MESSAGE_COLUMN_CANDIDATES:
            cols.append(col)
            continue
        parsed = _try_parse_list(sample.get(col))
        if parsed is not None and _looks_like_messages(parsed):
            cols.append(col)
    # Preserve order, dedupe.
    return list(dict.fromkeys(cols))


def _count_turns(value: Any) -> Optional[int]:
    parsed = _try_parse_list(value)
    return len(parsed) if parsed is not None else None


def _san(obj: Any) -> Any:
    """Make an arbitrary object JSON-serializable (handles numpy etc.)."""
    return json.loads(json.dumps(obj, default=str, ensure_ascii=False))


def _get_dataset(name: str, config: Optional[str], split: str, token: Optional[str]):
    key = (name, config, split)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    try:
        ds = load_dataset(name, config, split=split, token=token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to load dataset: {exc}") from exc
    with _LOCK:
        _CACHE[key] = ds
    return ds


@app.get("/api/load")
def load(
    dataset: str = Query(..., description="HF dataset id or URL"),
    config: Optional[str] = Query(None),
    split: Optional[str] = Query(None),
    x_hf_token: Optional[str] = Header(None),
):
    """Load (and cache) a dataset split and return its metadata."""
    token = _resolve_token(x_hf_token)
    name = _normalize_dataset(dataset)
    if not name:
        raise HTTPException(status_code=400, detail="Empty dataset name")

    try:
        configs = get_dataset_config_names(name, token=token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"Could not read dataset '{name}'. Check the name and your token. ({exc})",
        ) from exc

    config = config or (configs[0] if configs else None)

    try:
        splits = get_dataset_split_names(name, config, token=token) if config else []
    except Exception:
        splits = []
    split = split or (splits[0] if splits else "train")

    ds = _get_dataset(name, config, split, token)
    msg_cols = _detect_message_columns(ds)

    return {
        "dataset": name,
        "config": config,
        "split": split,
        "configs": configs,
        "splits": splits,
        "num_rows": len(ds),
        "columns": list(ds.column_names),
        "message_columns": msg_cols,
    }


@app.get("/api/rows")
def rows(
    dataset: str = Query(...),
    config: Optional[str] = Query(None),
    split: str = Query("train"),
    offset: int = Query(0, ge=0),
    length: int = Query(50, ge=1, le=200),
    x_hf_token: Optional[str] = Header(None),
):
    """Return a page of rows with the heavy message columns stripped out.

    Each row keeps only scalar/metadata columns plus a `_<col>_turns` count for
    every detected message column, keeping the list view fast.
    """
    token = _resolve_token(x_hf_token)
    name = _normalize_dataset(dataset)
    ds = _get_dataset(name, config, split, token)
    msg_cols = set(_detect_message_columns(ds))

    n = len(ds)
    end = min(offset + length, n)
    out: list[dict] = []
    if offset < end:
        batch = ds[offset:end]
        for i in range(end - offset):
            idx = offset + i
            meta: dict[str, Any] = {}
            for col in ds.column_names:
                val = batch[col][i]
                if col in msg_cols:
                    meta[f"_{col}_turns"] = _count_turns(val)
                else:
                    meta[col] = val
            out.append({"idx": idx, "row": _san(meta)})

    return {"num_rows_total": n, "offset": offset, "length": end - offset, "rows": out}


@app.get("/api/row")
def row(
    dataset: str = Query(...),
    config: Optional[str] = Query(None),
    split: str = Query("train"),
    idx: int = Query(..., ge=0),
    x_hf_token: Optional[str] = Header(None),
):
    """Return a single full (untruncated) row, including all message columns."""
    token = _resolve_token(x_hf_token)
    name = _normalize_dataset(dataset)
    ds = _get_dataset(name, config, split, token)
    if idx >= len(ds):
        raise HTTPException(status_code=404, detail=f"Row {idx} out of range (n={len(ds)})")
    full = ds[idx]
    return {
        "idx": idx,
        "row": _san(full),
        "message_columns": _detect_message_columns(ds),
    }


@app.get("/api/health")
def health():
    return {"ok": True, "cached": len(_CACHE)}

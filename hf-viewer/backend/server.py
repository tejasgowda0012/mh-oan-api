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
from pathlib import Path
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

# Local JSONL working copies. When a dataset has been exported here (via
# export_jsonl.py), the viewer reads/edits it locally and deletes are persisted
# to the file. The Hugging Face Hub is never modified.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Cache of local rows keyed by path -> (mtime, rows, columns, message_columns).
_LOCAL_CACHE: dict[str, tuple] = {}

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


def _detect_msg_cols(column_names: list[str], sample: dict) -> list[str]:
    cols: list[str] = []
    for col in column_names:
        if col in MESSAGE_COLUMN_CANDIDATES:
            cols.append(col)
            continue
        parsed = _try_parse_list(sample.get(col))
        if parsed is not None and _looks_like_messages(parsed):
            cols.append(col)
    # Preserve order, dedupe.
    return list(dict.fromkeys(cols))


def _detect_message_columns(ds) -> list[str]:
    sample = ds[0] if len(ds) > 0 else {}
    return _detect_msg_cols(ds.column_names, sample)


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


def _local_path(name: str) -> Path:
    """Path to the local JSONL working copy for a dataset id."""
    safe = name.replace("/", "__")
    return DATA_DIR / f"{safe}.jsonl"


def _load_local(path: Path) -> tuple[list[dict], list[str], list[str]]:
    """Load (and cache) a local JSONL file. Returns (rows, columns, msg_cols).

    The cache is invalidated automatically when the file's mtime changes (e.g.
    after a delete rewrites it).
    """
    key = str(path)
    mtime = path.stat().st_mtime
    with _LOCK:
        cached = _LOCAL_CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1], cached[2], cached[3]

    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # Union of keys, preserving the order they first appear.
    columns: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in columns:
                columns.append(k)
    sample = rows[0] if rows else {}
    msg_cols = _detect_msg_cols(columns, sample)

    with _LOCK:
        _LOCAL_CACHE[key] = (mtime, rows, columns, msg_cols)
    return rows, columns, msg_cols


def _rewrite_local(path: Path, rows: list[dict]) -> None:
    """Atomically rewrite the local JSONL and drop the cache entry."""
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    tmp.replace(path)
    with _LOCK:
        _LOCAL_CACHE.pop(str(path), None)


@app.get("/api/load")
def load(
    dataset: str = Query(..., description="HF dataset id or URL"),
    config: Optional[str] = Query(None),
    split: Optional[str] = Query(None),
    x_hf_token: Optional[str] = Header(None),
):
    """Load (and cache) a dataset split and return its metadata.

    If a local JSONL working copy exists for this dataset (created by
    export_jsonl.py), it is served from disk and becomes editable (deletable).
    Otherwise the dataset is read from the Hugging Face Hub (read-only).
    """
    token = _resolve_token(x_hf_token)
    name = _normalize_dataset(dataset)
    if not name:
        raise HTTPException(status_code=400, detail="Empty dataset name")

    local = _local_path(name)
    if local.exists():
        rows, columns, msg_cols = _load_local(local)
        return {
            "dataset": name,
            "config": config or "default",
            "split": split or "train",
            "configs": [],
            "splits": [],
            "num_rows": len(rows),
            "columns": columns,
            "message_columns": msg_cols,
            "source": "local",
        }

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
        "source": "hub",
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

    local = _local_path(name)
    if local.exists():
        rows, columns, msg_cols_list = _load_local(local)
        msg_cols = set(msg_cols_list)
        n = len(rows)
        end = min(offset + length, n)
        out: list[dict] = []
        for idx in range(offset, end):
            r = rows[idx]
            meta: dict[str, Any] = {}
            for col in columns:
                val = r.get(col)
                if col in msg_cols:
                    meta[f"_{col}_turns"] = _count_turns(val)
                else:
                    meta[col] = val
            out.append({"idx": idx, "row": _san(meta)})
        return {
            "num_rows_total": n,
            "offset": offset,
            "length": max(0, end - offset),
            "rows": out,
            "source": "local",
        }

    ds = _get_dataset(name, config, split, token)
    msg_cols = set(_detect_message_columns(ds))

    n = len(ds)
    end = min(offset + length, n)
    out = []
    if offset < end:
        batch = ds[offset:end]
        for i in range(end - offset):
            idx = offset + i
            meta = {}
            for col in ds.column_names:
                val = batch[col][i]
                if col in msg_cols:
                    meta[f"_{col}_turns"] = _count_turns(val)
                else:
                    meta[col] = val
            out.append({"idx": idx, "row": _san(meta)})

    return {
        "num_rows_total": n,
        "offset": offset,
        "length": end - offset,
        "rows": out,
        "source": "hub",
    }


def _row_summary(idx: int, r: dict, columns: list[str], msg_cols: set[str]) -> dict:
    """Lightweight row metadata for list/search views."""
    meta: dict[str, Any] = {}
    for col in columns:
        val = r.get(col)
        if col in msg_cols:
            meta[f"_{col}_turns"] = _count_turns(val)
        else:
            meta[col] = val
    return {"idx": idx, "row": _san(meta)}


@app.get("/api/find")
def find_sessions(
    dataset: str = Query(...),
    config: Optional[str] = Query(None),
    split: str = Query("train"),
    session_id: str = Query(..., min_length=1, description="Full or partial session_id"),
    limit: int = Query(25, ge=1, le=100),
    x_hf_token: Optional[str] = Header(None),
):
    """Find conversations by session_id (case-insensitive substring match)."""
    token = _resolve_token(x_hf_token)
    name = _normalize_dataset(dataset)
    needle = session_id.strip().lower()
    if not needle:
        raise HTTPException(status_code=400, detail="session_id query is empty")

    matches: list[dict] = []

    local = _local_path(name)
    if local.exists():
        rows, columns, msg_cols_list = _load_local(local)
        msg_cols = set(msg_cols_list)
        for idx, r in enumerate(rows):
            sid = str(r.get("session_id", ""))
            if needle in sid.lower():
                matches.append(_row_summary(idx, r, columns, msg_cols))
                if len(matches) >= limit:
                    break
        return {
            "query": session_id,
            "num_matches": len(matches),
            "truncated": len(matches) >= limit,
            "matches": matches,
            "source": "local",
        }

    ds = _get_dataset(name, config, split, token)
    msg_cols = set(_detect_message_columns(ds))
    columns = list(ds.column_names)
    for idx in range(len(ds)):
        r = ds[idx]
        sid = str(r.get("session_id", ""))
        if needle in sid.lower():
            matches.append(_row_summary(idx, r, columns, msg_cols))
            if len(matches) >= limit:
                break

    return {
        "query": session_id,
        "num_matches": len(matches),
        "truncated": len(matches) >= limit,
        "matches": matches,
        "source": "hub",
    }


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

    local = _local_path(name)
    if local.exists():
        rows, _columns, msg_cols = _load_local(local)
        if idx >= len(rows):
            raise HTTPException(status_code=404, detail=f"Row {idx} out of range (n={len(rows)})")
        return {
            "idx": idx,
            "row": _san(rows[idx]),
            "message_columns": msg_cols,
            "source": "local",
        }

    ds = _get_dataset(name, config, split, token)
    if idx >= len(ds):
        raise HTTPException(status_code=404, detail=f"Row {idx} out of range (n={len(ds)})")
    full = ds[idx]
    return {
        "idx": idx,
        "row": _san(full),
        "message_columns": _detect_message_columns(ds),
        "source": "hub",
    }


@app.delete("/api/row")
def delete_row(
    dataset: str = Query(...),
    session_id: str = Query(..., description="session_id of the conversation to delete"),
):
    """Delete a whole conversation from the local JSONL working copy.

    Only the local file is modified; the Hugging Face Hub is never touched. The
    dataset must have been exported locally first (via export_jsonl.py).
    """
    name = _normalize_dataset(dataset)
    local = _local_path(name)
    if not local.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                "Delete is only available for local JSONL datasets. "
                "Export the dataset first with export_jsonl.py."
            ),
        )

    rows, _columns, _msg_cols = _load_local(local)
    target = str(session_id)
    kept = [r for r in rows if str(r.get("session_id")) != target]
    if len(kept) == len(rows):
        raise HTTPException(status_code=404, detail=f"session_id '{session_id}' not found")

    _rewrite_local(local, kept)
    return {"deleted": session_id, "removed": len(rows) - len(kept), "remaining": len(kept)}


@app.get("/api/health")
def health():
    return {"ok": True, "cached": len(_CACHE), "local_cached": len(_LOCAL_CACHE)}

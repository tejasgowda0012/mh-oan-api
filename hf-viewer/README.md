# HF Dataset Conversation Viewer

A small UI to load a **Hugging Face dataset** (by id or URL) and review every
conversation in it — including all **tool calls** and **tool returns** — and to
**delete bad conversations locally** while validating the data.

Built for inspecting/validating the synthetic training data in this repo
(e.g. `kenpath/mh-synthetic-v1`), but it works with any dataset that has a
conversation / messages column.

```
hf-viewer/
├── backend/            # FastAPI — serves rows from a local JSONL or the HF Hub
│   ├── server.py
│   ├── export_jsonl.py # one-time: pull an HF dataset into a local JSONL copy
│   └── requirements.txt
├── frontend/           # Next.js + Tailwind UI
│   ├── app/            # pages (list + conversation detail)
│   ├── components/     # table, timeline, tool-call / tool-return blocks, loader
│   └── lib/            # api client, message parser, types
├── data/               # local JSONL working copies (git-ignored)
├── run.sh              # starts backend (:8100) + frontend (:3100) together
└── README.md
```

---

## Two modes: local (editable) vs hub (read-only)

The viewer works in one of two modes per dataset, decided automatically by the
backend:

- **Local mode** — if a local JSONL working copy exists at
  `hf-viewer/data/<owner>__<name>.jsonl`, the backend reads and edits **that
  file**. This is the mode you use to review and **delete** conversations.
  Deletions are written only to the local file; the Hugging Face Hub is never
  touched. The UI shows a green **"local copy · editable"** badge and a trash
  icon on each conversation.
- **Hub mode** — if there is no local copy, the dataset is loaded directly from
  the Hugging Face Hub and is **read-only** (no delete). The UI shows a
  **"hub · read-only"** badge.

To go from hub mode to local mode, run the one-time export below.

---

## How it works

1. **Backend** (`backend/server.py`) serves rows from the local JSONL when
   present, otherwise from the HF Hub via the `datasets` library (cached in
   memory). It exposes:
   - `GET /api/load`  — load a dataset, return metadata (configs, splits,
     columns, row count, detected conversation columns, and `source`:
     `"local"` or `"hub"`)
   - `GET /api/rows`  — a page of rows with the heavy message columns stripped
     out (only metadata + a turn count), for the list view
   - `GET /api/row`   — a single **full, untruncated** row, for the detail view
   - `DELETE /api/row?dataset=...&session_id=...` — remove a whole conversation
     from the **local JSONL** (rewrites the file atomically). Returns `400` if
     no local copy exists (export first).
2. **Frontend** (`frontend/`) talks to the backend (proxied via Next.js
   rewrites), shows a sortable/paginated table of conversations, renders each
   transcript with user/assistant bubbles, tool calls (arguments) and
   collapsible tool returns, and — in local mode — provides trash icons to
   delete conversations.

> Why a backend instead of the public HF datasets-server API? That API
> **truncates large cells**, which would corrupt the messages JSON and drop
> tool-call/return content. Loading via `datasets` returns the full data.

The viewer auto-detects the conversation column and supports two formats:

- **OpenAI chat-completions** (this repo's `synthetic/prepare_dataset.py`
  output): `[{role, content, tool_calls, ...}]`
- **pydantic-ai message history** (raw `*_messages_json` columns):
  `[{parts: [{part_kind, content, tool_name, args, ...}]}]`

---

## Prerequisites

- Python 3.11+ with the repo virtualenv (`.venv`) — it already has `fastapi`,
  `uvicorn`, `datasets` and `huggingface_hub` installed.
- Node.js 18+ (for the frontend).
- A **Hugging Face token** if the dataset is private or gated
  (`kenpath/mh-synthetic-v1` is private). Needed to **load from the Hub** and to
  **export** a local copy. You can paste the token in the UI or set it as an
  environment variable.
  - The token must belong to an account with access to the dataset. For private
    `kenpath/*` datasets, use a token from a member of the **`kenpath`** org —
    in this repo's `.env` that is `HUGGINGFACE_WRITE_TOKEN` (the plain
    `HF_TOKEN` there does **not** have access). `run.sh` and the export script
    pick the right one automatically.
  - Note: a token is **not** required to browse/delete an already-exported local
    copy.

---

## Quick start (recommended)

From the **project root** (`mh-oan-api/`):

```bash
./hf-viewer/run.sh
```

`run.sh` automatically loads an HF token from the repo `.env`
(`HUGGINGFACE_WRITE_TOKEN`, falling back to `HF_TOKEN`). To override it, export
your own first:

```bash
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxx
./hf-viewer/run.sh
```

This starts:

- backend on `http://localhost:8100`
- frontend on `http://localhost:3100`

The script installs frontend dependencies automatically on first run.

Open `http://localhost:3100`, the dataset field is pre-filled with
`kenpath/mh-synthetic-v1` — click **Load dataset**.

---

## Manual start (two terminals)

**Terminal 1 — backend** (run from inside `backend/`, because the folder name
`hf-viewer` is not a valid Python module path):

```bash
cd hf-viewer/backend
../../.venv/bin/uvicorn server:app --port 8100 --reload
```

> If the venv is already activated, you can just run `uvicorn server:app
> --port 8100 --reload`.

**Terminal 2 — frontend:**

```bash
cd hf-viewer/frontend
npm install        # first time only
npm run dev        # serves on http://localhost:3100
```

---

## Export a local copy (required for deleting)

To review and delete conversations, first export the dataset into a local JSONL
working copy. Run from the **project root**:

```bash
./.venv/bin/python hf-viewer/backend/export_jsonl.py kenpath/mh-synthetic-v1
```

This writes `hf-viewer/data/kenpath__mh-synthetic-v1.jsonl` (one JSON record per
line) and switches the viewer into **local mode** for that dataset.

Options:

| Flag         | Default            | Purpose                                  |
|--------------|--------------------|------------------------------------------|
| `--config`   | first available    | Dataset config name.                     |
| `--split`    | `train`            | Split to export.                         |
| `--force`    | off                | Overwrite an existing JSONL (this **discards** any local deletions and re-pulls the full dataset). |

The export reads a token from `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` /
`HUGGINGFACE_WRITE_TOKEN` for private/gated datasets. `run.sh` sets this up; if
running the export standalone, export the token first (see Prerequisites).

The `data/` folder is git-ignored, so local copies and edits are never
committed.

---

## Using the viewer

1. **Enter a dataset** — a bare id (`owner/name`) or a full URL
   (`https://huggingface.co/datasets/owner/name`). Defaults to
   `kenpath/mh-synthetic-v1`.
2. **Add an HF token** (if needed for hub mode) — click *"Add HF token"*, paste
   it. It is stored in your browser only and sent with each request to the local
   backend.
3. **Load dataset** — you'll see total rows, the detected conversation
   column(s), config/split selectors (when there is more than one), and a
   **source badge**: green "local copy · editable" or "hub · read-only".
4. **Browse** the conversation table; click any row to open the transcript.
5. **Review the transcript:**
   - green = user, blue = assistant (rendered as markdown)
   - amber = tool call (with arguments)
   - gray, collapsible = tool return
   - the system prompt and any "thinking" parts are collapsed by default
   - if a dataset has multiple conversation columns, switch between them with
     the tabs at the top.

---

## Deleting conversations (local only)

Deleting requires **local mode** (export the dataset first). The Hugging Face
Hub is never modified — only the local JSONL changes.

- **From the list** — click the trash icon at the end of a row, then confirm.
  The row disappears and the count updates immediately.
- **From the transcript** — open a conversation and click the trash icon in the
  header, then confirm. You're returned to the list.

Each conversation is identified by its `session_id`; deleting rewrites the local
JSONL without that record.

When the data is finalized, the cleaned file lives at
`hf-viewer/data/<owner>__<name>.jsonl` and is ready to push back to Hugging Face
separately (the viewer intentionally does not push to HF). To start over from
the original dataset, re-run the export with `--force`.

---

## Configuration

| Variable                 | Where    | Purpose                                                      |
|--------------------------|----------|-------------------------------------------------------------|
| `HF_TOKEN`               | backend / export | Token for private/gated datasets (or use the UI).   |
| `HUGGING_FACE_HUB_TOKEN` | backend / export | Alternative token env var.                          |
| `HUGGINGFACE_WRITE_TOKEN`| backend / export | Org-scoped token used for private `kenpath/*` datasets (preferred by `run.sh` and the export script). |
| `HF_VIEWER_BACKEND`      | frontend | Backend URL for the proxy (default `http://localhost:8100`). |
| `HF_HOME`                | backend / export | Where `datasets` caches Hub downloads. `run.sh` points this at `hf-viewer/.hf_cache`. |

Default ports: backend `8100`, frontend `3100` (chosen to avoid clashing with
the existing `viewer/` app on `8000`/`3000`).

---

## Troubleshooting

- **"Address already in use" (port 8100 or 3100)** — a previous server is still
  running. Free the port and restart:
  `lsof -ti tcp:8100 tcp:3100 | xargs kill -9`.
- **`no such file or directory: .../uvicorn`** — check the path. From inside
  `backend/` the venv is two levels up: `../../.venv/bin/uvicorn`. If the venv is
  activated, just run `uvicorn ...`.
- **Delete button is missing / "Delete is only available for local JSONL
  datasets"** — you're in hub (read-only) mode. Export a local copy first.
- **"The dataset does not exist, or is not accessible without authentication"** —
  the dataset is private/gated; add your HF token (and make sure your account
  has access).
- **List/detail fails to load** — confirm the backend is running on `:8100`
  (`curl http://localhost:8100/api/health`).
- **First Hub load is slow** — `datasets` downloads the files on the first
  request, then caches them; subsequent browsing is fast. (Local mode reads the
  JSONL directly and is always fast.)
- **"No conversation column detected"** — the dataset has no recognizable
  messages column. Column auto-detection looks for common names
  (`messages`, `conversation`, `*_messages_json`, ...) and any column whose
  values parse as a list of message objects.

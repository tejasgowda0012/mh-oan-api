# HF Dataset Conversation Viewer

A small UI to load a **Hugging Face dataset** (by id or URL) and review every
conversation in it, including all **tool calls** and **tool returns**.

Built for inspecting/validating the synthetic training data in this repo
(e.g. `kenpath/mh-synthetic-v1`), but it works with any dataset that has a
conversation / messages column.

```
hf-viewer/
├── backend/        # FastAPI — loads the dataset and serves untruncated rows
│   ├── server.py
│   └── requirements.txt
├── frontend/       # Next.js + Tailwind UI
│   ├── app/        # pages (list + conversation detail)
│   ├── components/ # table, timeline, tool-call / tool-return blocks, loader
│   └── lib/        # api client, message parser, types
├── run.sh          # starts backend (:8100) + frontend (:3100) together
└── README.md
```

---

## How it works

1. **Backend** (`backend/server.py`) uses the `datasets` library to load a
   dataset split and cache it in memory. It exposes:
   - `GET /api/load`  — load a dataset, return metadata (configs, splits,
     columns, row count, detected conversation columns)
   - `GET /api/rows`  — a page of rows with the heavy message columns stripped
     out (only metadata + a turn count), for the list view
   - `GET /api/row`   — a single **full, untruncated** row, for the detail view
2. **Frontend** (`frontend/`) talks to the backend (proxied via Next.js
   rewrites), shows a sortable/paginated table of conversations, and renders
   each transcript with user/assistant bubbles, tool calls (arguments) and
   collapsible tool returns.

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
  (`kenpath/mh-synthetic-v1` is private). You can paste the token in the UI or
  set it as an environment variable.
  - The token must belong to an account with access to the dataset. For private
    `kenpath/*` datasets, use a token from a member of the **`kenpath`** org —
    in this repo's `.env` that is `HUGGINGFACE_WRITE_TOKEN` (the plain
    `HF_TOKEN` there does **not** have access). `run.sh` picks the right one
    automatically.

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

**Terminal 2 — frontend:**

```bash
cd hf-viewer/frontend
npm install        # first time only
npm run dev        # serves on http://localhost:3100
```

---

## Using the viewer

1. **Enter a dataset** — a bare id (`owner/name`) or a full URL
   (`https://huggingface.co/datasets/owner/name`). Defaults to
   `kenpath/mh-synthetic-v1`.
2. **Add an HF token** (if needed) — click *"Add HF token"*, paste it. It is
   stored in your browser only and sent with each request to the local backend.
3. **Load dataset** — you'll see total rows, the detected conversation
   column(s), and config/split selectors (when there is more than one).
4. **Browse** the conversation table; click any row to open the transcript.
5. **Review the transcript:**
   - green = user, blue = assistant (rendered as markdown)
   - amber = tool call (with arguments)
   - gray, collapsible = tool return
   - the system prompt and any "thinking" parts are collapsed by default
   - if a dataset has multiple conversation columns, switch between them with
     the tabs at the top.

---

## Configuration

| Variable             | Where      | Purpose                                            |
|----------------------|------------|----------------------------------------------------|
| `HF_TOKEN`           | backend    | Token for private/gated datasets (or use the UI).  |
| `HUGGING_FACE_HUB_TOKEN` | backend | Alternative token env var.                         |
| `HF_VIEWER_BACKEND`  | frontend   | Backend URL for the proxy (default `http://localhost:8100`). |

Default ports: backend `8100`, frontend `3100` (chosen to avoid clashing with
the existing `viewer/` app on `8000`/`3000`).

---

## Troubleshooting

- **"The dataset does not exist, or is not accessible without authentication"** —
  the dataset is private/gated; add your HF token (and make sure your account
  has access).
- **List/detail fails to load** — confirm the backend is running on `:8100`
  (`curl http://localhost:8100/api/health`).
- **First load is slow** — `datasets` downloads the files on the first request,
  then caches them; subsequent browsing is fast.
- **"No conversation column detected"** — the dataset has no recognizable
  messages column. Column auto-detection looks for common names
  (`messages`, `conversation`, `*_messages_json`, ...) and any column whose
  values parse as a list of message objects.

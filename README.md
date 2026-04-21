# pocketwatchai

A personal-finance analyst for bank statements. Upload a statement (CSV, XLSX, or PDF - text or scanned), get instant analytics, and chat with an LLM-backed agent that can answer questions grounded in your actual transactions.

The backend is a FastAPI service that parses statements with a multi-strategy pipeline (pdfplumber ruled tables → word-position reconstruction → Tesseract OCR fallback), auto-detects the statement currency, and builds summary + chart-ready analytics. The frontend is a Vite + React + TypeScript SPA with a dashboard, a chart view, and a persistent chat interface wired to a LangChain tool-using agent. Find the web application [here](https://pocketwatchai-opal.vercel.app)

---

## Main features

### 1. Upload any common statement format

CSV, XLSX, and PDF are all accepted. PDF handling is the interesting part: many bank PDFs have no ruled tables (Revolut), some have rules but collapse every row into a single cell (FirstBank), and scanned statements have no extractable text at all. The loader tries multiple strategies and picks the best one via a quality score.

- PDF loader + strategy selection: [`backend/app.py` `_load_pdf_tables_and_text`](backend/app.py), [`_extract_tables_from_pdf_page`](backend/app.py)
- Word-position reconstruction (works for PDFs without ruled tables): [`backend/app.py` `_extract_transactions_from_words`](backend/app.py)
- Generic frame loader (CSV / XLSX / PDF dispatch): [`backend/app.py` `_load_dataframe`](backend/app.py)
- Frontend upload widget: [`frontend/src/components/dashboard/FileUpload.tsx`](frontend/src/components/dashboard/FileUpload.tsx)
- Sample statements used during development: [`sample_statements/`](sample_statements)

### 2. OCR fallback for scanned PDFs

Pages with no extractable text are rendered with [pypdfium2](https://pypi.org/project/pypdfium2/) and OCR'd with [pytesseract](https://pypi.org/project/pytesseract/). The recognized words are fed back into the same word-position transaction parser used for text PDFs, so the downstream pipeline is unchanged.

- OCR helpers (`_ocr_available`, `_ocr_page_words`, `_ocr_page_text`, `_locate_tesseract_binary`): [`backend/app.py`](backend/app.py)
- Install instructions for the Tesseract binary: [OCR setup](#ocr-setup-optional-but-required-for-scanned-pdfs) below

There is no page limit and no wall-clock budget by default — `MAX_PDF_PAGES` and `PDF_TIME_BUDGET_SECONDS` can still be set as optional safety limits.

### 3. Dynamic currency detection

The statement's currency is inferred from the raw text, column headers, and cell values — symbols (`$ € £ ¥ ₦ …`), prefixed codes (`US$`, `A$`, `NZ$` …), and ISO codes (`USD`, `NGN` …) are all scored and the most frequent hint wins. If nothing is detected, the frontend shows no currency prefix rather than a misleading default.

- Detector: [`backend/app.py` `_detect_currency`](backend/app.py)
- Frontend render: [`frontend/src/components/dashboard/SummaryCards.tsx`](frontend/src/components/dashboard/SummaryCards.tsx)

### 4. Analytics dashboard

The backend produces summary metrics (total income, total expenses, net balance), a category breakdown, and a daily spending series. The frontend renders these as cards and recharts visualizations.

- Analytics builder and data cleaning: [`backend/app.py` `_prepare_financial_df`](backend/app.py), [`_build_analytics`](backend/app.py)
- Summary cards: [`frontend/src/components/dashboard/SummaryCards.tsx`](frontend/src/components/dashboard/SummaryCards.tsx)
- Category pie chart: [`frontend/src/components/dashboard/CategoryChart.tsx`](frontend/src/components/dashboard/CategoryChart.tsx)
- Daily spending chart: [`frontend/src/components/dashboard/SpendingChart.tsx`](frontend/src/components/dashboard/SpendingChart.tsx)
- Dashboard page (glues everything + handles stale sessions): [`frontend/src/pages/Dashboard.tsx`](frontend/src/pages/Dashboard.tsx)
- Type contracts: [`frontend/src/types/analytics.ts`](frontend/src/types/analytics.ts)

### 5. Chat with a tool-using LangChain agent

`POST /api/chat` routes the user's question to one of three tools: a statement-aware calculator / lookup over the uploaded frame, a timeless finance-education LLM call, or a real-time web search via DuckDuckGo. If `OPENAI_API_KEY` isn't set, a heuristic fallback still answers basic questions.

- Chat endpoint + routing + tools: [`backend/app.py` `/api/chat`](backend/app.py)
- Model call + tool registration (`_chat_with_model`, `general_finance_timeless`, `general_finance_realtime_web`): [`backend/app.py`](backend/app.py)
- Chat UI and message flow: [`frontend/src/components/chat/ChatInterface.tsx`](frontend/src/components/chat/ChatInterface.tsx), [`frontend/src/pages/Chat.tsx`](frontend/src/pages/Chat.tsx)
- API client: [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts)

### 6. Persistent, multi-conversation chat history

Conversations (messages, timestamps, titles) survive page reloads. The Zustand store is persisted to `localStorage` and `Date` fields are revived on hydration.

- Store and persistence config: [`frontend/src/store/useAppStore.ts`](frontend/src/store/useAppStore.ts)
- Conversation sidebar UI: [`frontend/src/components/layout/AppSidebar.tsx`](frontend/src/components/layout/AppSidebar.tsx)
- Chat types: [`frontend/src/types/chat.ts`](frontend/src/types/chat.ts)

### 7. Graceful session handling

Sessions live in-memory on the backend, so they disappear on restart. The frontend detects a stale `sessionId` from `localStorage` (404 from `/api/analytics/{id}`), clears it silently, and lets the user upload a fresh statement without a leftover error banner.

- Recovery logic: [`frontend/src/pages/Dashboard.tsx`](frontend/src/pages/Dashboard.tsx)

---

## Project tree

```
cursor3/
├── backend/
│   ├── app.py                    # FastAPI app, PDF/CSV/XLSX ingest, OCR, analytics, chat agent
│   ├── Dockerfile                # Backend image (includes Tesseract binary for OCR)
│   └── requirements.txt          # Python dependencies (FastAPI, pdfplumber, pytesseract, pypdfium2, LangChain, ...)
│
├── frontend/
│   ├── .env.example              # VITE_API_BASE_URL
│   ├── index.html
│   ├── package.json              # Vite + React + TS + Tailwind + Radix + Zustand + Recharts
│   ├── tailwind.config.ts
│   ├── tsconfig*.json
│   ├── vite.config.ts
│   ├── vitest.config.ts
│   └── src/
│       ├── App.tsx               # Router + top-level providers
│       ├── main.tsx
│       ├── index.css
│       ├── components/
│       │   ├── chat/
│       │   │   └── ChatInterface.tsx       # Chat view, message list, send handler
│       │   ├── dashboard/
│       │   │   ├── CategoryChart.tsx       # Recharts category breakdown
│       │   │   ├── FileUpload.tsx          # Drag/drop + POST /api/upload
│       │   │   ├── SpendingChart.tsx       # Daily spending line chart
│       │   │   └── SummaryCards.tsx        # Income / Expenses / Net balance cards
│       │   ├── layout/
│       │   │   ├── AppLayout.tsx
│       │   │   ├── AppSidebar.tsx          # Persisted conversation list
│       │   │   └── TopNav.tsx
│       │   ├── ui/                         # shadcn/ui primitives (Radix + Tailwind)
│       │   └── NavLink.tsx
│       ├── hooks/                          # use-mobile, use-toast
│       ├── lib/
│       │   ├── api.ts                      # uploadFinancialDocument, fetchAnalytics, sendChat
│       │   ├── userProfile.ts
│       │   └── utils.ts
│       ├── pages/
│       │   ├── Chat.tsx
│       │   ├── Dashboard.tsx               # Upload + analytics view
│       │   ├── Index.tsx
│       │   ├── Login.tsx
│       │   ├── NotFound.tsx
│       │   └── Signup.tsx
│       ├── store/
│       │   └── useAppStore.ts              # Zustand + persist middleware
│       ├── test/                           # Vitest setup + example test
│       └── types/
│           ├── analytics.ts
│           └── chat.ts
│
├── sample_statements/            # Real-world PDFs used for development/testing
│   ├── revolut_sample_statement.pdf
│   ├── revolut_sample_statement_removed.pdf
│   ├── sample_statement.pdf
│   └── uba_sample_statement.pdf
│
├── render.yaml                   # Render Blueprint (Docker-based deploy)
├── LICENSE
└── README.md
```

---

## Getting started

Prerequisites:

- Python 3.10+
- Node.js 18+ (or Bun)
- Optional: Tesseract OCR binary (only needed to parse scanned PDFs) — see [OCR setup](#ocr-setup-optional-but-required-for-scanned-pdfs)

### Backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# (optional) enable the LLM-backed chat agent
#   PowerShell:  $env:OPENAI_API_KEY = "sk-..."
#   bash:        export OPENAI_API_KEY=sk-...

uvicorn app:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000` and the OpenAPI docs at `http://127.0.0.1:8000/docs`.

### Frontend

```bash
cd frontend
npm install            # or: bun install
cp .env.example .env   # edits VITE_API_BASE_URL if your backend is not on :8000
npm run dev            # or: bun run dev
```

The dev server runs at `http://localhost:5173` by default.

### Build

```bash
cd frontend
npm run build          # static build into frontend/dist
```

### Tests

```bash
cd frontend
npm test               # Vitest
```

---

## OCR setup (optional, but required for scanned PDFs)

`pytesseract` is only a Python wrapper. The actual OCR engine is a native binary (`tesseract`) that must be installed separately on whatever machine runs the backend.

### Local development

- Windows: install from [UB-Mannheim builds](https://github.com/UB-Mannheim/tesseract/wiki). The default path `C:\Program Files\Tesseract-OCR\tesseract.exe` is auto-detected. Otherwise, set `TESSERACT_CMD` to the full path.
- macOS: `brew install tesseract`
- Ubuntu / Debian: `sudo apt-get install tesseract-ocr`

Auto-discovery is implemented in [`backend/app.py` `_locate_tesseract_binary`](backend/app.py).

### Deployed environments (Render, Fly.io, Railway, Docker, etc.)

Managed Python runtimes (Render Native, Heroku-style buildpacks) cannot install OS-level packages. Use the Docker image in this repo instead — it installs `tesseract-ocr` into the container.

- [`backend/Dockerfile`](backend/Dockerfile) — backend image with Tesseract + all Python deps baked in.
- [`render.yaml`](render.yaml) — Render Blueprint that deploys that Dockerfile as a web service and wires the OCR-related env vars.

On Render specifically: push this repo, create a new Blueprint in the Render dashboard pointing at it, and Render picks `render.yaml` up automatically. You'll need to paste your `OPENAI_API_KEY` secret in the dashboard (it's left as `sync: false` on purpose).

### Verifying OCR is wired up on the server

After deploy, hit the diagnostics endpoint:

```
GET https://<your-backend>/api/ocr-status
```

It returns JSON like this when everything is good:

```json
{
  "available": true,
  "tesseract_cmd": "/usr/bin/tesseract",
  "tesseract_on_path": true,
  "tesseract_version": "5.3.0",
  "error": null,
  "env": { "TESSERACT_CMD": "/usr/bin/tesseract", "OCR_DPI": "250", ... }
}
```

If `available: false`, the `install_hint` field tells you exactly what's missing. Implementation: [`backend/app.py` `/api/ocr-status`](backend/app.py).

### Quality and speed tuning

OCR-related env vars (all optional):

| Variable | Default | Effect |
|---|---|---|
| `OCR_DPI` | `200` | Rendering resolution fed to Tesseract. `200` is the memory-safe default (peak RSS ~250 MB on a 28-page A4 scan); `300` is sharper and needs roughly 2.3x the RAM; `400+` rarely helps. |
| `TESSERACT_LANG` | `eng` | Comma-separated languages (e.g. `eng+fra`). Requires the matching `tesseract-ocr-<lang>` package to also be installed. |
| `TESSERACT_CONFIG` | `--oem 1 --psm 6 -c preserve_interword_spaces=1` | Tesseract CLI flags. `--psm 6` treats each page as a single text block (correct for statements). Change only if you know what you're doing. |
| `TESSERACT_CMD` | auto-detected | Absolute path to the binary. |

Images are also preprocessed before OCR (grayscale + autocontrast + median denoise + threshold) — this alone roughly doubles digit accuracy on typical bank scans. Implementation: [`backend/app.py` `_preprocess_for_ocr`](backend/app.py).

Without the binary, text-based PDFs (Revolut, FirstBank, etc.), CSVs, and XLSX files all still work — only scanned PDFs require OCR, and if the binary is missing the API returns a single actionable error message pointing here.

---

## Environment variables

Backend (`backend/app.py`):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | unset | Enables the LangChain tool-using chat agent. Without it, a heuristic fallback answers basic questions. |
| `TESSERACT_CMD` | auto-detected | Explicit path to the Tesseract binary for OCR. |
| `TESSERACT_LANG` | `eng` | Language(s) passed to Tesseract, e.g. `eng+fra`. |
| `TESSERACT_CONFIG` | `--oem 1 --psm 6 -c preserve_interword_spaces=1` | Tesseract CLI flags. |
| `OCR_DPI` | `200` | Render DPI for OCR. Lower = faster + less memory, higher = more accurate. Raise to `300` on hosts with >= 1 GB RAM. |
| `MAX_PDF_PAGES` | `100000` (effectively unlimited) | Optional hard cap on pages processed per PDF. |
| `PDF_TIME_BUDGET_SECONDS` | unset (no budget) | Optional wall-clock cap on PDF parsing time. |
| `MAX_UPLOAD_BYTES` | `31457280` (30 MB) | Max upload size. |

Frontend (`frontend/.env`):

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Base URL the SPA calls. |

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe. |
| `GET` | `/api/ocr-status` | Reports whether Tesseract OCR is available on this instance. |
| `POST` | `/api/upload` | Multipart upload (CSV / XLSX / PDF). Returns `sessionId` + analytics payload. |
| `GET` | `/api/analytics/{session_id}` | Re-fetch analytics for a previous session. Returns 404 if the session has expired. |
| `POST` | `/api/chat` | JSON `{ question, sessionId?, history? }`. Returns `{ answer, route, tool }`. |

Routes are defined in [`backend/app.py`](backend/app.py).

---

## How a statement flows through the system

1. `FileUpload` posts the file to `POST /api/upload`.
2. `_load_dataframe` dispatches by content type:
   - CSV / XLSX: pandas reads directly.
   - PDF: `_load_pdf_tables_and_text` walks every page. For text pages it runs `_extract_tables_from_pdf_page` (which tries several pdfplumber strategies + a word-position reconstruction, scored by `_table_quality_score`). For image-only pages it renders with pypdfium2 and OCRs with Tesseract, then feeds the OCR words into the same `_extract_transactions_from_words` parser.
3. `_detect_currency` scans the raw text + column headers for currency hints.
4. `_prepare_financial_df` normalizes columns (amount, date, description, category) and cleans rows.
5. `_build_analytics` computes the summary cards, category breakdown, and daily spending series.
6. The frame is stashed in the in-memory `SESSIONS` dict under a new `sessionId`.
7. The frontend stores `sessionId` in `localStorage` and renders the dashboard.
8. Follow-up chat questions (`POST /api/chat`) are routed through a LangChain agent that can read from the stored frame, call a timeless-finance LLM prompt, or hit DuckDuckGo for current events.

---

## License

See [LICENSE](LICENSE).

# Uncia Sense — Invoice Factoring Verification

A Streamlit application that uses Anthropic Claude or DeepSeek (selectable in the sidebar) to analyze invoice factoring submission packages and produce two outputs:

1. **FactorSQL upload (CSV)** — one row per receivable in FactorSQL's expected column order, ready to import.
2. **Underwriting analysis (Excel)** — multi-sheet workbook with documents, packages, discrepancies, red flags, match matrix, missing docs, and unassigned items.

The system handles **PDF, XML (CFDI), JPG, and PNG** documents in **English, Spanish, or bilingual** form. It identifies multiple receivables per submission, matches supporting documents (POs, BLs, NoAs, buyer-portal screenshots) to invoices, correctly extracts the **receivable balance** net of any prepayments, and flags fraud risks.

---

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- **Anthropic API key** — get one at <https://console.anthropic.com/>
- **DeepSeek API key** (optional) — get one at <https://platform.deepseek.com/>
- **Tesseract OCR** (recommended, Windows) — needed only for scanned PDFs and phone-photo images. Born-digital PDFs work without it.

---

## Setup

### 1. Activate the virtual environment

A venv is already created at `.venv/`. VS Code is configured to use it automatically. For a manual terminal:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

Installs: `anthropic`, `streamlit`, `pandas`, `openpyxl`, `python-dotenv`, `pymupdf`, `pdfplumber`, `pytesseract`, `langdetect`, `pillow`.

### 3. Configure your API keys

Copy the example env file and fill in your keys:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```ini
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...   # optional — only needed if you select a DeepSeek model
```

`ANTHROPIC_API_KEY` is required (the default model is Claude Opus 4.7). `DEEPSEEK_API_KEY` is only consulted when a DeepSeek model is selected from the sidebar model dropdown.

### 4. (Recommended) Install Tesseract OCR

Local preprocessing extracts text from PDFs and images before sending to Claude. Born-digital PDFs work with the pure-pip stack (PyMuPDF). **Scanned PDFs and phone photos need Tesseract** — without it, those files fall back to being sent as base64 to Claude (same as the old pipeline) and you'll see a warning in the progress log.

1. Download the UB Mannheim Windows build: https://github.com/UB-Mannheim/tesseract/wiki
2. During install, check the **English** and **Spanish** language packs (the app uses `eng+spa`).
3. Either let the installer add it to `PATH` (default location is `C:\Program Files\Tesseract-OCR\tesseract.exe`) or set `TESSERACT_CMD` in your environment:

   ```powershell
   $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

The app auto-detects Tesseract from `TESSERACT_CMD`, then common install paths, then `PATH`. If none work, scans are silently routed to Claude's vision instead.

---

## Run

```powershell
streamlit run app.py
```

Streamlit opens the app at <http://localhost:8501>.

---

## Usage

1. In the sidebar, upload one or more submission documents (PDF / XML / JPG / PNG).
2. Confirm or edit the auto-generated **Submission ID** (used as the filename stem for the two downloads).
3. Pick a **Model** (default Opus 4.7), toggle **Local preprocessing** (default on), and choose a **Prompt** variant — Fast (~30–60 s) or Full (~90–120 s+).
4. Choose **Response language** — English or Spanish. Affects free-text fields only (underwriter summaries, descriptions, notes, reasons). JSON keys and enum values (`APPROVE`, `HIGH`, `FAVORABLE`, etc.) always stay in English. Party names and product descriptions remain verbatim in the source document's language.
5. Click **Analyze**. The expanded status widget shows a verbose log (per-file preprocessing milestones, Tesseract banner, retry notices) and the **Live response from Claude** stream box renders the JSON token-by-token (~5× per second).
6. Review results:
   - **Top metrics** — document count, receivables, orientation issues, total advance eligible, overall recommendation
   - **Cost panel** — input/output/cache tokens with USD breakdown for the selected model
   - **Packages tab** — per-receivable: total / prepayment / amount due, confidence, advance amount, discrepancies, red flags, missing docs, match matrix
   - **Documents tab** — per-document: language, orientation, OCR quality, doc notes
   - **Unassigned tab** — documents that couldn't be matched to a receivable
   - **Preprocessed tab** — downloadable per-file `.md` artifacts produced by the local preprocessor (plus a ZIP)
   - **Raw JSON tab** — full underwriting report
   - **Conversation tab** — system prompt + sanitized user message blocks
   - **Response tab** — raw streamed text with download
7. Download the two output files:
   - **⬇ FactorSQL Upload (CSV)** — `{submission_id}_factorsql.csv`
   - **⬇ Analysis Report (Excel)** — `{submission_id}_analysis.xlsx`

---

## Outputs

### 1. FactorSQL CSV (`{submission_id}_factorsql.csv`)

One row per receivable. UTF-8 with BOM (so Excel opens accented characters correctly). Columns in FactorSQL's expected order:

| Column     | Source                                              |
|------------|-----------------------------------------------------|
| ACCT_ID    | (blank — filled by FactorSQL)                       |
| ACCT_SUB   | (blank — filled by FactorSQL)                       |
| BAL_ASSIGN | `amount_due` — receivable balance net of prepayment |
| DTR_NAME   | `buyer.name`                                        |
| DUE_DATE   | `due_date` (ISO 8601)                               |
| INV_DATE   | `invoice_date` (ISO 8601)                           |
| INV_ID     | `invoice_number`                                    |
| PO_NO      | `po_reference`                                      |
| REL_ID     | (blank — filled by FactorSQL)                       |

**Receivable balance:** `BAL_ASSIGN` is the actual receivable balance — `total_amount − prepayment_amount`. If the invoice shows a deposit / advance / down payment ("Less Deposit", "Anticipo", "Pago a Cuenta", "Menos Anticipo"), Claude subtracts it from the gross total so the factor finances only what's still owed.

All receivables are included regardless of recommendation — declined invoices ship with their balance so nothing is silently dropped. Filter the CSV before uploading if your program rules require it.

### 2. Underwriting analysis Excel (`{submission_id}_analysis.xlsx`)

Eight sheets:

- **Summary** — submission ID, totals, overall recommendation
- **Documents** — per-document metadata (filename, doc_type, language, orientation, OCR quality, scan/photo flag)
- **Packages** — per-receivable: invoice fields including **Total Amount**, **Prepayment Amount**, **Amount Due**, buyer/seller, PO, advance amount, recommendation, underwriter summary
- **Discrepancies** — flagged differences with direction (FAVORABLE / NEUTRAL / ADVERSE) and severity
- **Red Flags** — fraud / risk rules triggered, with severity
- **Match Matrix** — cross-document field comparison (invoice_number, total/amount_due, buyer, seller, PO, due_date)
- **Missing Documents** — required-but-absent doc types per receivable
- **Unassigned** — documents that couldn't be matched to any receivable

---

## Sidebar controls

- **Model selector** — options across two providers (default Opus 4.7):
  - Anthropic: Opus 4.7, Opus 4.6, Sonnet 4.6, Haiku 4.5
  - DeepSeek: V4 Flash (`deepseek-v4-flash`), V4 Pro (`deepseek-v4-pro`), and the deprecated V3 (`deepseek-chat`) and R1 (`deepseek-reasoner`) aliases
- **Local preprocessing** toggle — disable to A/B-compare against the pure-vision pipeline (Anthropic only; DeepSeek is text-only and ignores the toggle)
- **Prompt selector** — Fast (default, ~2,400 tokens, ~30–60 s typical) or Full (~6,100 tokens, ~90–120 s+ typical)

---

## Two prompt variants

- **Fast** (default) — `prompts/system_v2_fast.txt` · ~2,400 tokens

  Essentials: document types, multi-invoice grouping, Spanish↔English normalization, CFDI basics, preprocessing trust, receivable balance, 11 red-flag rules, focused 6-field match matrix, one-sentence underwriter summary. Same JSON output schema as Full but Claude is told to skip / null low-value fields.

- **Full** — `prompts/system_v2.txt` · ~6,100 tokens

  29 fraud rules, full taxonomy (A–G), per-field Spanish normalization table, detailed CFDI rules, cross-doc match matrix on ~25 fields, multi-paragraph underwriter summary. Most thorough but slower.

Both produce the same JSON shape, so downloads, tabs, and the Excel report work identically with either.

---

## Providers — Anthropic vs DeepSeek

The model dropdown routes to one of two providers automatically. The JSON output schema is identical; only the API mechanics differ.

### Anthropic (Claude) — full vision

- Models: Opus 4.7 (default), Opus 4.6, Sonnet 4.6, Haiku 4.5
- Pricing per 1M tokens (input / output): Opus $5 / $25, Sonnet $3 / $15, Haiku $1 / $5
- Vision: full — original PDFs and images are attached as fallback when local extraction confidence < 0.85
- Max output: 50,000 tokens
- Prompt caching: `cache_control: ephemeral` on the system prompt

### DeepSeek — text-only, much cheaper

- Models: V4 Flash (`deepseek-v4-flash`), V4 Pro (`deepseek-v4-pro`); the legacy `deepseek-chat` (V3) and `deepseek-reasoner` (R1) IDs still work but are deprecated by DeepSeek (they now alias the non-thinking and thinking modes of V4 Flash).
- Pricing per 1M tokens (input cache-miss / cache-hit / output): V4 Flash $0.14 / $0.0028 / $0.28; V4 Pro $0.435 / $0.003625 / $0.87 (75%-off promo through 2026/05/31 — 4× higher after expiry); legacy V3 $0.27 / $0.07 / $1.10; legacy R1 $0.55 / $0.14 / $2.19. Roughly **~10–40× cheaper than Claude Opus**.
- **Vision: not supported.** DeepSeek receives only the preprocessed Markdown. If a document is Tier B/C (low extraction confidence with a vision fallback), the fallback PDF/image is replaced with an inline note saying it can't be viewed. **Best with born-digital PDFs and clean scans** where Tier A coverage is high.
- Max output: 8,000 tokens. Multi-invoice packages with 8+ receivables may truncate — keep submissions under ~6–7 receivables when using DeepSeek, or switch to Anthropic.
- Prompt caching: automatic (DeepSeek-side context cache); cache-hit tokens are billed at the lower rate.
- Output mode: `response_format={"type": "json_object"}` to enforce JSON-only output.
- Uses the OpenAI Python SDK pointed at `https://api.deepseek.com`.

When a DeepSeek model is selected, the up-front status log shows `model=deepseek-v4-flash (deepseek)` so you know which provider is being called. Retry logic with 5/15/30 s backoff applies to both providers.

---

## Project structure

```
.
├── app.py                          # Streamlit UI
├── api_client.py                   # Claude API client with prompt caching + variant selection
├── file_handler.py                 # PDF/image/XML → Claude content blocks (text + optional vision fallback)
├── pdf_processor.py                # Local PDF/image preprocessing (PyMuPDF + Tesseract OSD + OCR)
├── excel_exporter.py               # FactorSQL CSV + multi-sheet Excel analysis
├── prompts/
│   ├── system_v2.txt               # Full system prompt (v2.2)
│   └── system_v2_fast.txt          # Fast system prompt (v2.2-fast, demo default)
├── requirements.txt
├── .env.example
└── .vscode/settings.json           # Auto-activates venv in VS Code terminals
```

---

## Implementation notes

- **Prompt caching**: Anthropic — each variant's system prompt is cached via `cache_control: ephemeral`. DeepSeek — automatic provider-side context cache; cache-hit tokens are billed at a discounted rate. Switching variants or providers is a one-time cache miss.
- **Model**: default `claude-opus-4-7`. `temperature` is intentionally omitted (removed on Opus 4.7). Sonnet 4.6 does not support assistant message prefill — handled automatically per-model. DeepSeek selection requires `DEEPSEEK_API_KEY` in `.env`; missing key fails the request with a clear error.
- **DeepSeek vs Anthropic**: same prompt, same JSON schema. DeepSeek is text-only — vision-fallback document/image blocks are replaced with an inline note so DeepSeek won't see the original. For best DeepSeek results, ensure local preprocessing is on (default) and Tesseract is installed for scanned docs.
- **Local preprocessing** (default on): PDFs and images are extracted to Markdown locally with PyMuPDF (and Tesseract for scans), sent as a text block prefaced with a ```json metadata header (filename, page count, language detected, OCR quality, rotation applied, extraction confidence, fallback flag). The original file is attached as a vision fallback only when extraction confidence < 0.85 or the source is a phone photo. Disable from the sidebar to A/B-compare against the pure-vision pipeline.
- **XML files**: always pass through inline as text with a `=== FILE: <name> ===` marker (CFDI XML is the legal source of truth — no preprocessing needed).
- **Receivable balance**: factoring critical. The prompt extracts `total_amount` (gross), `prepayment_amount` (deposits/advances shown on the invoice), and `amount_due = total_amount − prepayment_amount`. `BAL_ASSIGN` in the FactorSQL CSV and `advance_eligible_amount` in the JSON both use `amount_due`, not `total_amount`.
- **Response language**: per-submission English/Spanish toggle injected into the user message (not the system prompt) so prompt caching stays warm regardless of choice.
- **Max tokens**: 50,000 output — supports multi-invoice packages with up to ~10 receivables. For larger batches, pre-split per receivable.
- **OCR**: Tesseract with `eng+spa` language packs. Word confidence ≥ 80 → HIGH, 60–80 → MEDIUM, < 60 → LOW. LOW-quality pages always attach the original PDF/image as a fallback so Claude can use vision to fill gaps.
- **Output schema**: no separate `compliance` object. CFDI-stamping, sanctions, jurisdiction concerns surface through `red_flags` rules.

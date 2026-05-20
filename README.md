# Invoice Factoring Verification Engine

A Streamlit application that uses Claude (`claude-opus-4-7`) to analyze invoice factoring submission packages and produce a strict JSON underwriting report, viewable in the UI and downloadable as an Excel workbook.

Supports PDF, XML (CFDI), JPG, and PNG documents. Identifies multiple receivables per submission, matches supporting documents to invoices, detects orientation issues, and flags fraud/compliance risks.

---

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- **Anthropic API key** — get one at https://console.anthropic.com/
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

### 3. Configure your API key

Copy the example env file and fill in your key:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 4. (Recommended) Install Tesseract OCR

Local preprocessing extracts text from PDFs and images before sending to Claude. Born-digital PDFs work with the pure-pip stack (PyMuPDF). **Scanned PDFs and phone photos need Tesseract** — without it, those files fall back to being sent as base64 to Claude (same as the old pipeline) and you'll see a warning in the status log.

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

Streamlit will open the app at http://localhost:8501.

---

## Usage

1. In the sidebar, upload one or more submission documents (PDF / XML / JPG / PNG).
2. Confirm or edit the auto-generated **Submission ID**.
3. (Optional) Add **Submission Notes** for context.
4. (Optional) Toggle **Local preprocessing** in the sidebar. On by default — extracts text/tables locally with PyMuPDF + Tesseract and sends compact Markdown to Claude. Disable to A/B-compare against the pure-vision pipeline.
5. Click **Analyze**. Claude will process the package and return an underwriting report.
6. Review results:
   - **Top metrics** — document count, receivables, orientation issues, total advance eligible, overall recommendation
   - **Packages tab** — per-receivable: confidence, advance amount, discrepancies, red flags, missing docs, match matrix
   - **Documents tab** — per-document: language, orientation, OCR quality, doc notes
   - **Unassigned tab** — documents that couldn't be matched to a receivable
   - **Raw JSON tab** — the full report
7. Click **⬇ Download Excel Report** to export an 8-sheet workbook.

---

## Project structure

```
.
├── app.py                          # Streamlit UI
├── api_client.py                   # Claude API client with prompt caching
├── file_handler.py                 # PDF/image/XML → content block conversion
├── pdf_processor.py                # Local PDF/image preprocessing (PyMuPDF + Tesseract)
├── excel_exporter.py               # Multi-sheet Excel generation
├── prompts/
│   └── system_v2.txt               # Cached system prompt (v2.1)
├── requirements.txt
├── .env.example
└── .vscode/settings.json           # Auto-activates venv in VS Code terminals
```

---

## Notes

- **Prompt caching**: the system prompt is cached via `cache_control: ephemeral`, reducing cost on repeat submissions.
- **Model**: `claude-opus-4-7`. `temperature` is intentionally omitted (removed on Opus 4.7).
- **Local preprocessing** (default on): PDFs and images are extracted to Markdown locally and sent as a text block prefaced with a ```json metadata header. The original file is attached as a vision fallback only when extraction confidence is < 0.85 or the source is a phone photo. Disable from the sidebar to fall back to the pure-vision pipeline.
- **XML files**: always passed through inline as text with a `=== FILE: <name> ===` marker (CFDI XML is the legal source of truth — no preprocessing needed).
- **Max tokens**: 50,000 — supports multi-invoice packages with up to ~10 receivables. For larger batches, pre-split per receivable.
- **OCR**: Tesseract with `eng+spa` language packs. Confidence ≥ 80 → HIGH, 60–80 → MEDIUM, < 60 → LOW. LOW-quality pages always attach the original PDF/image as a fallback so Claude can use vision to fill gaps.

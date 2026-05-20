# Invoice Factoring Verification Engine

A Streamlit application that uses Claude (`claude-opus-4-7`) to analyze invoice factoring submission packages and produce a strict JSON underwriting report, viewable in the UI and downloadable as an Excel workbook.

Supports PDF, XML (CFDI), JPG, and PNG documents. Identifies multiple receivables per submission, matches supporting documents to invoices, detects orientation issues, and flags fraud/compliance risks.

---

## Prerequisites

- **Python 3.10+** (tested on 3.14)
- **Anthropic API key** — get one at https://console.anthropic.com/

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
4. Click **Analyze**. Claude will process the package and return an underwriting report.
5. Review results:
   - **Top metrics** — document count, receivables, orientation issues, total advance eligible, overall recommendation
   - **Packages tab** — per-receivable: confidence, advance amount, discrepancies, red flags, missing docs, match matrix, compliance
   - **Documents tab** — per-document: language, orientation, OCR quality, doc notes
   - **Unassigned tab** — documents that couldn't be matched to a receivable
   - **Raw JSON tab** — the full report
6. Click **⬇ Download Excel Report** to export an 8-sheet workbook.

---

## Project structure

```
.
├── app.py                          # Streamlit UI
├── api_client.py                   # Claude API client with prompt caching
├── file_handler.py                 # PDF/image/XML → content block conversion
├── excel_exporter.py               # Multi-sheet Excel generation
├── prompts/
│   └── system_v2.txt               # Cached system prompt (v2.0)
├── requirements.txt
├── .env.example
└── .vscode/settings.json           # Auto-activates venv in VS Code terminals
```

---

## Notes

- **Prompt caching**: the system prompt is cached via `cache_control: ephemeral`, reducing cost on repeat submissions.
- **Model**: `claude-opus-4-7`. `temperature` is intentionally omitted (removed on Opus 4.7).
- **XML files**: embedded inline as text with a `=== FILE: <name> ===` marker. PDFs go in as document blocks (base64). Images go in as image blocks (base64).
- **Max tokens**: 16,000 — supports multi-invoice packages with up to ~10 receivables. For larger batches, pre-split per receivable.

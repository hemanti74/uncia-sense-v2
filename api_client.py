import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import anthropic
from dotenv import load_dotenv

from file_handler import build_content_blocks

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("factoring_verification")

_PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VARIANTS: dict[str, dict] = {
    "fast": {"file": "system_v2_fast.txt", "label": "Fast (default)"},
    "full": {"file": "system_v2.txt",      "label": "Full (detailed)"},
}
DEFAULT_PROMPT_VARIANT = "fast"

# Per-million-token pricing in USD. Cache read = 10% of input; cache write = 125% of input.
MODEL_PRICING: dict[str, dict] = {
    "claude-opus-4-7":   {"label": "Opus 4.7 (most capable)",       "input": 5.0, "output": 25.0, "prefill": False},
    "claude-opus-4-6":   {"label": "Opus 4.6",                       "input": 5.0, "output": 25.0, "prefill": True},
    "claude-sonnet-4-6": {"label": "Sonnet 4.6 (balanced)",          "input": 3.0, "output": 15.0, "prefill": False},
    "claude-haiku-4-5":  {"label": "Haiku 4.5 (fastest, cheapest)",  "input": 1.0, "output": 5.0, "prefill": True},
}

DEFAULT_MODEL = "claude-opus-4-7"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _extract_json(text: str) -> str:
    """Strip whitespace, optional ```json fences, and any prose around a JSON object."""
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            s = s[start:end + 1]
    return s


def _sanitize_blocks_for_display(blocks: list[dict]) -> list[dict]:
    """Strip base64 payloads from document/image blocks so they're safe to render."""
    out = []
    for b in blocks:
        btype = b.get("type")
        if btype == "document":
            src = b.get("source") or {}
            data = src.get("data") or ""
            out.append({
                "type": "document",
                "title": b.get("title", ""),
                "source": {
                    "type": src.get("type"),
                    "media_type": src.get("media_type"),
                    "data": f"<base64 omitted: {_human_size(len(data) * 3 // 4)}>",
                },
            })
        elif btype == "image":
            src = b.get("source") or {}
            data = src.get("data") or ""
            out.append({
                "type": "image",
                "source": {
                    "type": src.get("type"),
                    "media_type": src.get("media_type"),
                    "data": f"<base64 omitted: {_human_size(len(data) * 3 // 4)}>",
                },
            })
        elif btype == "text":
            text = b.get("text", "") or ""
            if len(text) > 20_000:
                out.append({
                    "type": "text",
                    "text": text[:20_000] + f"\n…\n<truncated for display: {len(text):,} chars total>",
                })
            else:
                out.append(b)
        else:
            out.append(b)
    return out


def _compute_cost(model: str, usage) -> Optional[dict]:
    """Compute USD cost from token usage. Returns None if pricing unknown."""
    pricing = MODEL_PRICING.get(model)
    if not pricing or usage is None:
        return None

    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    input_rate = pricing["input"] / 1_000_000
    output_rate = pricing["output"] / 1_000_000
    cache_read_rate = input_rate * 0.10
    cache_write_rate = input_rate * 1.25

    input_cost = in_tok * input_rate
    output_cost = out_tok * output_rate
    cache_read_cost = cache_read * cache_read_rate
    cache_write_cost = cache_write * cache_write_rate
    total = input_cost + output_cost + cache_read_cost + cache_write_cost

    return {
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cache_read_cost": cache_read_cost,
        "cache_write_cost": cache_write_cost,
        "total_cost": total,
    }


def analyze_submission(
    files: list[tuple[str, bytes]],
    submission_id: str,
    notes: str = "",
    model: str = DEFAULT_MODEL,
    progress_cb: Optional[Callable[[str], None]] = None,
    preprocess: bool = True,
    stream_cb: Optional[Callable[[str], None]] = None,
    response_language: str = "en",
    prompt_variant: str = DEFAULT_PROMPT_VARIANT,
) -> tuple[str, Optional[dict], dict]:
    def report(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    variant = prompt_variant if prompt_variant in PROMPT_VARIANTS else DEFAULT_PROMPT_VARIANT
    report(
        f"Starting submission {submission_id} with {len(files)} file(s) · "
        f"model={model} · prompt={variant} · "
        f"preprocess={'on' if preprocess else 'off'} · "
        f"response_lang={(response_language or 'en').lower()}"
    )

    if preprocess:
        from pdf_processor import get_tesseract_status

        status = get_tesseract_status()
        if status["available"]:
            ver = f" v{status['version']}" if status["version"] else ""
            report(f"Tesseract OCR: ✓ available{ver} ({status['path']})")
        else:
            report(
                "Tesseract OCR: ✗ not installed — scanned/photo files will be sent "
                "as base64 to Claude (vision fallback). Install UB Mannheim build "
                "and add eng+spa packs, or set $env:TESSERACT_CMD."
            )

    system_text = (_PROMPTS_DIR / PROMPT_VARIANTS[variant]["file"]).read_text(encoding="utf-8")
    client = anthropic.Anthropic()

    content_blocks = []
    preprocessed_artifacts: list[dict] = []
    for idx, (name, data) in enumerate(files, 1):
        ext = Path(name).suffix.lower()
        size = _human_size(len(data))
        report(f"[{idx}/{len(files)}] Processing {name} ({ext}, {size})")
        blocks, extracted = build_content_blocks(
            name, data, preprocess=preprocess, progress_cb=report
        )
        content_blocks.extend(blocks)
        if extracted is not None:
            preprocessed_artifacts.append({
                "filename": extracted.filename,
                "kind": extracted.kind,
                "markdown": extracted.markdown,
                "metadata": extracted.metadata,
                "confidence": extracted.confidence,
                "fallback_attached": extracted.fallback_needed,
            })

    note_line = f"Submission notes: {notes}\n" if notes.strip() else ""

    lang = (response_language or "en").lower()
    if lang == "es":
        language_directive = (
            "\n\nOutput language: SPANISH. Write all human-readable free-text fields "
            "(underwriter_summary, every description, doc_notes, every note, every "
            "reason, every disposition, matching_issues) in Spanish. JSON keys and "
            "enum values MUST remain in English exactly as specified in the schema — "
            "this includes recommendation values (APPROVE / APPROVE_WITH_NOTE / "
            "REVIEW / DECLINE / INSUFFICIENT_DOCS), severity (LOW / MEDIUM / HIGH / "
            "CRITICAL), direction (FAVORABLE / NEUTRAL / ADVERSE), match-matrix "
            "status (MATCH / VARIANCE / FAIL / NOT_APPLICABLE), content_orientation, "
            "ocr_quality, and all schema field names. Preserve party names, "
            "addresses, and product descriptions verbatim in their original document "
            "language."
        )
    else:
        language_directive = (
            "\n\nOutput language: ENGLISH. Write all human-readable free-text fields "
            "in English. Preserve party names, addresses, and product descriptions "
            "verbatim in their original document language."
        )

    content_blocks.append(
        {
            "type": "text",
            "text": (
                f"Submission ID: {submission_id}\n"
                f"{note_line}"
                f"\nAnalyze the attached {len(files)} document(s) and return the JSON "
                "underwriting report per your system instructions. The submission may "
                "contain one or multiple receivables — identify each independently and "
                "group supporting documents accordingly."
                f"{language_directive}"
            ),
        }
    )

    use_prefill = (MODEL_PRICING.get(model) or {}).get("prefill", False)
    messages = [{"role": "user", "content": content_blocks}]
    if use_prefill:
        messages.append({"role": "assistant", "content": "{"})

    report(f"Sending {len(content_blocks) - 1} document(s) for analysis (streaming)…")
    text_parts: list[str] = []
    chars = 0
    # Spinner ticks every 5 s; init to 0.0 so the first chunk fires it immediately.
    SPINNER_INTERVAL_S = 5.0
    last_tick = 0.0
    last_stream_tick = 0.0
    prefix = "{" if use_prefill else ""
    spinner_frames = ("|", "/", "-", "\\")
    spinner_idx = 0
    with client.messages.stream(
        model=model,
        max_tokens=50000,
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            text_parts.append(text)
            chars += len(text)
            now = time.monotonic()
            if now - last_tick >= SPINNER_INTERVAL_S:
                report(f"Awaiting response {spinner_frames[spinner_idx % 4]}")
                spinner_idx += 1
                last_tick = now
            if stream_cb and now - last_stream_tick >= 0.2:
                stream_cb(prefix + "".join(text_parts))
                last_stream_tick = now
        if stream_cb:
            stream_cb(prefix + "".join(text_parts))
        final_message = stream.get_final_message()

    usage = getattr(final_message, "usage", None)
    cost = _compute_cost(model, usage)

    if usage:
        report(
            f"Response complete · {chars:,} chars · "
            f"input tokens: {getattr(usage, 'input_tokens', '?')} · "
            f"output tokens: {getattr(usage, 'output_tokens', '?')} · "
            f"cache read: {getattr(usage, 'cache_read_input_tokens', 0)} · "
            f"cache write: {getattr(usage, 'cache_creation_input_tokens', 0)}"
        )
    if cost:
        report(f"Estimated cost: ${cost['total_cost']:.4f} USD")

    raw = ("{" if use_prefill else "") + "".join(text_parts)
    conversation = {
        "model": model,
        "system": system_text,
        "user_message": _sanitize_blocks_for_display(content_blocks),
        "assistant_response": raw,
        "preprocessed_documents": preprocessed_artifacts,
    }
    return raw, cost, conversation


def parse_report(raw: str) -> dict:
    """Parse the model's raw JSON response, falling back to fence/object extraction."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_extract_json(raw))

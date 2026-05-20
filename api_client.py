import json
import logging
import os
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

# Per-million-token pricing in USD.
# Anthropic cache read = 10% of input; cache write = 125% of input (hardcoded).
# DeepSeek explicit cache_hit rate; cache_write does not apply.
MODEL_PRICING: dict[str, dict] = {
    "claude-opus-4-7":   {"label": "Opus 4.7 (most capable)",       "input": 5.0,  "output": 25.0, "prefill": False, "provider": "anthropic"},
    "claude-opus-4-6":   {"label": "Opus 4.6",                      "input": 5.0,  "output": 25.0, "prefill": True,  "provider": "anthropic"},
    "claude-sonnet-4-6": {"label": "Sonnet 4.6 (balanced)",         "input": 3.0,  "output": 15.0, "prefill": False, "provider": "anthropic"},
    "claude-haiku-4-5":  {"label": "Haiku 4.5 (fastest, cheapest)", "input": 1.0,  "output": 5.0,  "prefill": True,  "provider": "anthropic"},
    "deepseek-v4-flash": {"label": "DeepSeek V4 Flash",             "input": 0.14, "output": 0.28, "prefill": False, "provider": "deepseek", "cache_hit": 0.0028},
    # `deepseek-v4-pro` rates reflect a 75%-off promo extended until 2026/05/31 15:59 UTC; after expiry, multiply each rate by 4.
    "deepseek-v4-pro":   {"label": "DeepSeek V4 Pro",               "input": 0.435,"output": 0.87, "prefill": False, "provider": "deepseek", "cache_hit": 0.003625},
    # Deprecated by DeepSeek: `deepseek-chat` and `deepseek-reasoner` now alias the non-thinking and thinking modes of `deepseek-v4-flash`.
    "deepseek-chat":     {"label": "DeepSeek V3 (chat)",            "input": 0.27, "output": 1.10, "prefill": False, "provider": "deepseek", "cache_hit": 0.07},
    "deepseek-reasoner": {"label": "DeepSeek R1 (reasoning)",       "input": 0.55, "output": 2.19, "prefill": False, "provider": "deepseek", "cache_hit": 0.14},
}

DEFAULT_MODEL = "claude-opus-4-7"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MAX_OUTPUT_TOKENS = 8000   # DeepSeek output cap is 8K
ANTHROPIC_MAX_OUTPUT_TOKENS = 50000

# Retry policy for transient server errors (overloaded / 5xx / rate limit / timeout).
# Backoff schedule: 5s, 15s, 30s — up to 4 total attempts. Tunable.
STREAM_MAX_ATTEMPTS = 4
STREAM_BACKOFF_SECONDS = (5.0, 15.0, 30.0)


def _is_retriable_error(exc: Exception) -> bool:
    """True for transient errors (Anthropic or OpenAI/DeepSeek) safe to retry."""
    msg = str(exc).lower()
    if "overloaded" in msg or "rate_limit" in msg or "rate limit" in msg:
        return True
    if "internal_server" in msg or "internal server" in msg:
        return True
    if "timeout" in msg or "connection" in msg:
        return True
    for sdk_name in ("anthropic", "openai"):
        try:
            mod = __import__(sdk_name)
            retriable = tuple(
                t for t in (
                    getattr(mod, "InternalServerError", None),
                    getattr(mod, "RateLimitError", None),
                    getattr(mod, "APIConnectionError", None),
                    getattr(mod, "APITimeoutError", None),
                ) if t is not None
            )
            if retriable and isinstance(exc, retriable):
                return True
        except Exception:
            continue
    return False


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


def _usage_to_dict(usage) -> dict:
    """Coerce an Anthropic / OpenAI usage object into a plain dict."""
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            d = dict(usage.model_dump())
        except Exception:
            d = {}
        extra = getattr(usage, "model_extra", None) or {}
        d.update(extra)
        return d
    if isinstance(usage, dict):
        return dict(usage)
    # Fallback — best-effort attribute scrape
    out = {}
    for attr in (
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
        "prompt_tokens", "completion_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    ):
        v = getattr(usage, attr, None)
        if v is not None:
            out[attr] = v
    return out


def _compute_cost(model: str, usage) -> Optional[dict]:
    """Compute USD cost from token usage. Returns None if pricing unknown."""
    pricing = MODEL_PRICING.get(model)
    if not pricing or usage is None:
        return None

    provider = pricing.get("provider", "anthropic")
    u = _usage_to_dict(usage)

    if provider == "anthropic":
        in_tok = u.get("input_tokens", 0) or 0
        out_tok = u.get("output_tokens", 0) or 0
        cache_read = u.get("cache_read_input_tokens", 0) or 0
        cache_write = u.get("cache_creation_input_tokens", 0) or 0

        input_rate = pricing["input"] / 1_000_000
        output_rate = pricing["output"] / 1_000_000
        cache_read_rate = input_rate * 0.10
        cache_write_rate = input_rate * 1.25

        input_cost = in_tok * input_rate
        output_cost = out_tok * output_rate
        cache_read_cost = cache_read * cache_read_rate
        cache_write_cost = cache_write * cache_write_rate
        total = input_cost + output_cost + cache_read_cost + cache_write_cost

    elif provider == "deepseek":
        in_tok = u.get("prompt_tokens", 0) or 0
        out_tok = u.get("completion_tokens", 0) or 0
        cache_read = u.get("prompt_cache_hit_tokens", 0) or 0
        cache_miss = u.get("prompt_cache_miss_tokens", None)
        if cache_miss is None:
            cache_miss = max(in_tok - cache_read, 0)
        cache_write = 0

        input_rate = pricing["input"] / 1_000_000
        cache_hit_rate = pricing.get("cache_hit", pricing["input"] * 0.25) / 1_000_000
        output_rate = pricing["output"] / 1_000_000

        # `input_cost` here = cost of the cache-miss (non-cached) portion of the prompt.
        input_cost = cache_miss * input_rate
        cache_read_cost = cache_read * cache_hit_rate
        output_cost = out_tok * output_rate
        cache_write_cost = 0.0
        total = input_cost + cache_read_cost + output_cost
    else:
        return None

    return {
        "model": model,
        "provider": provider,
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


def _content_blocks_to_text(blocks: list[dict]) -> str:
    """Flatten Anthropic-style content blocks to a single string for text-only
    providers (DeepSeek). Vision-fallback document/image blocks become inline
    notes since DeepSeek can't see them."""
    parts: list[str] = []
    for b in blocks:
        btype = b.get("type")
        if btype == "text":
            t = b.get("text") or ""
            if t.strip():
                parts.append(t)
        elif btype == "document":
            title = b.get("title", "unknown")
            parts.append(
                f"\n[Attached PDF: {title} — original document NOT viewable by this model "
                "(text-only provider). Use the preprocessor's extracted Markdown above.]\n"
            )
        elif btype == "image":
            src = b.get("source") or {}
            media = src.get("media_type", "image")
            parts.append(
                f"\n[Attached {media} — NOT viewable by this model (text-only provider). "
                "Use the preprocessor's extracted text above.]\n"
            )
    return "\n\n".join(p for p in parts if p)


def _stream_anthropic_with_retry(
    client,
    model: str,
    system_text: str,
    messages: list,
    use_prefill: bool,
    report: Callable[[str], None],
    stream_cb: Optional[Callable[[str], None]],
    max_tokens: int = ANTHROPIC_MAX_OUTPUT_TOKENS,
) -> tuple[str, object]:
    """Stream from Anthropic with automatic retry. Returns (raw_text, final_message)."""
    SPINNER_INTERVAL_S = 5.0
    spinner_frames = ("|", "/", "-", "\\")
    prefix = "{" if use_prefill else ""

    for attempt in range(STREAM_MAX_ATTEMPTS):
        text_parts: list[str] = []
        last_tick = 0.0
        last_stream_tick = 0.0
        spinner_idx = 0
        if stream_cb and attempt > 0:
            stream_cb("")

        try:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    text_parts.append(text)
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
            return prefix + "".join(text_parts), final_message
        except Exception as e:
            is_last = attempt >= STREAM_MAX_ATTEMPTS - 1
            if is_last or not _is_retriable_error(e):
                raise
            wait_s = STREAM_BACKOFF_SECONDS[min(attempt, len(STREAM_BACKOFF_SECONDS) - 1)]
            report(
                f"Claude API busy ({type(e).__name__}); retrying in {wait_s:.0f}s "
                f"(attempt {attempt + 2}/{STREAM_MAX_ATTEMPTS})…"
            )
            time.sleep(wait_s)

    raise RuntimeError("Anthropic streaming exhausted retries")


def _stream_deepseek_with_retry(
    client,
    model: str,
    system_text: str,
    user_text: str,
    report: Callable[[str], None],
    stream_cb: Optional[Callable[[str], None]],
    max_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS,
) -> tuple[str, object]:
    """Stream from DeepSeek (OpenAI-compatible) with automatic retry.
    Returns (raw_text, final_usage)."""
    SPINNER_INTERVAL_S = 5.0
    spinner_frames = ("|", "/", "-", "\\")

    for attempt in range(STREAM_MAX_ATTEMPTS):
        text_parts: list[str] = []
        last_tick = 0.0
        last_stream_tick = 0.0
        spinner_idx = 0
        final_usage = None
        if stream_cb and attempt > 0:
            stream_cb("")

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                response_format={"type": "json_object"},
            )
            for chunk in response:
                # Final usage chunk often has empty choices
                if getattr(chunk, "usage", None):
                    final_usage = chunk.usage
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if not content:
                    continue
                text_parts.append(content)
                now = time.monotonic()
                if now - last_tick >= SPINNER_INTERVAL_S:
                    report(f"Awaiting response {spinner_frames[spinner_idx % 4]}")
                    spinner_idx += 1
                    last_tick = now
                if stream_cb and now - last_stream_tick >= 0.2:
                    stream_cb("".join(text_parts))
                    last_stream_tick = now
            if stream_cb:
                stream_cb("".join(text_parts))
            return "".join(text_parts), final_usage
        except Exception as e:
            is_last = attempt >= STREAM_MAX_ATTEMPTS - 1
            if is_last or not _is_retriable_error(e):
                raise
            wait_s = STREAM_BACKOFF_SECONDS[min(attempt, len(STREAM_BACKOFF_SECONDS) - 1)]
            report(
                f"DeepSeek API busy ({type(e).__name__}); retrying in {wait_s:.0f}s "
                f"(attempt {attempt + 2}/{STREAM_MAX_ATTEMPTS})…"
            )
            time.sleep(wait_s)

    raise RuntimeError("DeepSeek streaming exhausted retries")


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

    pricing = MODEL_PRICING.get(model) or {}
    provider = pricing.get("provider", "anthropic")

    variant = prompt_variant if prompt_variant in PROMPT_VARIANTS else DEFAULT_PROMPT_VARIANT
    report(
        f"Starting submission {submission_id} with {len(files)} file(s) · "
        f"model={model} ({provider}) · prompt={variant} · "
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
                "as base64 (or skipped, if provider is text-only). Install UB Mannheim "
                "build and add eng+spa packs, or set $env:TESSERACT_CMD."
            )

    if provider == "deepseek" and not preprocess:
        report(
            "WARN: DeepSeek is text-only — disabling local preprocessing would leave "
            "PDFs/images with no way through. Re-enable preprocessing."
        )

    system_text = (_PROMPTS_DIR / PROMPT_VARIANTS[variant]["file"]).read_text(encoding="utf-8")

    content_blocks: list[dict] = []
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

    report(f"Sending {len(content_blocks) - 1} document(s) for analysis (streaming, {provider})…")

    if provider == "anthropic":
        client = anthropic.Anthropic()
        use_prefill = pricing.get("prefill", False)
        messages = [{"role": "user", "content": content_blocks}]
        if use_prefill:
            messages.append({"role": "assistant", "content": "{"})
        raw, final_message = _stream_anthropic_with_retry(
            client, model, system_text, messages, use_prefill, report, stream_cb,
        )
        usage = getattr(final_message, "usage", None)
    elif provider == "deepseek":
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "openai package not installed. Run: pip install -r requirements.txt"
            )
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY not set. Add it to .env to use DeepSeek models."
            )
        client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        user_text = _content_blocks_to_text(content_blocks)
        raw, usage = _stream_deepseek_with_retry(
            client, model, system_text, user_text, report, stream_cb,
        )
    else:
        raise ValueError(f"Unknown provider for model {model}: {provider}")

    cost = _compute_cost(model, usage)

    if usage:
        u = _usage_to_dict(usage)
        if provider == "anthropic":
            report(
                f"Response complete · {len(raw):,} chars · "
                f"input: {u.get('input_tokens', '?')} · "
                f"output: {u.get('output_tokens', '?')} · "
                f"cache read: {u.get('cache_read_input_tokens', 0)} · "
                f"cache write: {u.get('cache_creation_input_tokens', 0)}"
            )
        else:  # deepseek
            report(
                f"Response complete · {len(raw):,} chars · "
                f"prompt: {u.get('prompt_tokens', '?')} (cache hit: {u.get('prompt_cache_hit_tokens', 0)}) · "
                f"completion: {u.get('completion_tokens', '?')}"
            )
    if cost:
        report(f"Estimated cost: ${cost['total_cost']:.4f} USD")

    conversation = {
        "model": model,
        "provider": provider,
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

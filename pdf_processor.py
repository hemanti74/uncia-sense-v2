"""Local PDF/image preprocessing: extract text, tables, and orientation locally so
Claude receives compact Markdown instead of base64 documents/images for every file.

Returns an ExtractedDocument with:
  - markdown: a single Markdown string (metadata header + per-page text + tables)
  - metadata: dict echoed in the markdown header (also useful for callers)
  - confidence: 0.0..1.0 extraction confidence
  - fallback_needed: True when confidence < 0.85 or content is skewed; caller should
    attach the original bytes as a vision fallback
  - original_bytes / media_type / kind: for the caller to assemble the fallback block
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("factoring_verification.preprocess")

EXTRACTOR_VERSION = "uncia-pre/0.1"

# Tiering thresholds (see plan §6)
TIER_A_MIN_CONFIDENCE = 0.85
TIER_B_MIN_CONFIDENCE = 0.60

# Born-digital heuristics
BORN_DIGITAL_MIN_WORDS = 30
IMAGE_AREA_FRACTION_THRESHOLD = 0.60

# OCR
OCR_DPI = 200
OCR_LANGUAGES = "eng+spa"
OCR_HIGH = 80
OCR_MEDIUM = 60

# Tesseract detection — populated lazily
_TESSERACT_CHECKED = False
_TESSERACT_AVAILABLE = False
_TESSERACT_PATH = ""


@dataclass
class ExtractedDocument:
    filename: str
    kind: str  # "pdf" | "image"
    markdown: str
    metadata: dict
    confidence: float
    fallback_needed: bool
    original_bytes: bytes
    media_type: str = ""
    warnings: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Tesseract resolution
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_tesseract() -> bool:
    """Locate tesseract.exe on Windows. Returns True if available."""
    global _TESSERACT_CHECKED, _TESSERACT_AVAILABLE, _TESSERACT_PATH
    if _TESSERACT_CHECKED:
        return _TESSERACT_AVAILABLE
    _TESSERACT_CHECKED = True

    try:
        import pytesseract  # noqa: F401
    except ImportError:
        logger.warning("pytesseract not installed; OCR unavailable")
        return False

    import pytesseract as _pt

    env_path = os.environ.get("TESSERACT_CMD")
    candidates = []
    if env_path:
        candidates.append(env_path)
    candidates.extend([
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ])

    for path in candidates:
        if path and Path(path).is_file():
            _pt.pytesseract.tesseract_cmd = path
            _TESSERACT_AVAILABLE = True
            _TESSERACT_PATH = path
            logger.info("Tesseract found at %s", path)
            return True

    try:
        _pt.get_tesseract_version()
        _TESSERACT_AVAILABLE = True
        _TESSERACT_PATH = "PATH"
        logger.info("Tesseract found on PATH")
        return True
    except Exception:
        logger.warning(
            "Tesseract binary not found. Scanned PDFs/images will fall back to "
            "sending the original to Claude. Install from "
            "https://github.com/UB-Mannheim/tesseract/wiki and add eng+spa packs, "
            "or set $env:TESSERACT_CMD to its full path."
        )
        return False


def get_tesseract_status() -> dict:
    """Public helper for callers (UI) to show whether OCR is wired up.

    Returns: { "available": bool, "path": str, "version": str | None }
    """
    available = _resolve_tesseract()
    version = None
    if available:
        try:
            import pytesseract
            version = str(pytesseract.get_tesseract_version())
        except Exception:
            version = None
    return {
        "available": available,
        "path": _TESSERACT_PATH if available else "",
        "version": version,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def extract_document(
    filename: str,
    file_bytes: bytes,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> ExtractedDocument:
    """Extract text/tables locally from a PDF or image. XML is not handled here."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(filename, file_bytes, progress_cb)
    if ext in (".jpg", ".jpeg", ".png"):
        return _extract_image(filename, file_bytes, ext, progress_cb)
    raise ValueError(f"pdf_processor cannot handle extension: {ext}")


# ──────────────────────────────────────────────────────────────────────────────
# PDF path
# ──────────────────────────────────────────────────────────────────────────────

def _extract_pdf(
    filename: str,
    file_bytes: bytes,
    progress_cb: Optional[Callable[[str], None]],
) -> ExtractedDocument:
    import fitz  # PyMuPDF

    _p = progress_cb or (lambda _msg: None)

    warnings: list[str] = []
    pages_md: list[str] = []
    page_confidences: list[float] = []
    page_qualities: list[str] = []
    any_ocr = False
    any_scan = False
    ocr_page_count = 0
    applied_rotations: list[int] = []
    metadata_rotations: list[int] = []

    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        page_count = len(doc)
        for idx in range(page_count):
            page = doc[idx]
            _p(f"  page {idx + 1}/{page_count}: extracting…")

            meta_rot = int(page.rotation or 0)
            metadata_rotations.append(meta_rot)

            born_digital = _is_born_digital(page)
            page_md_parts: list[str] = [f"## Page {idx + 1}"]
            page_conf = 1.0
            page_quality = "HIGH"
            applied_rotation = 0

            if born_digital:
                text = _extract_text_in_reading_order(page)
                if text.strip():
                    page_md_parts.append(text.strip())

                tables_md = _extract_tables_pdf(page, file_bytes, idx)
                if tables_md:
                    page_md_parts.append(tables_md)
            else:
                any_scan = True
                if not _resolve_tesseract():
                    warnings.append(
                        f"Page {idx + 1}: OCR unavailable; skipped. "
                        "Attaching original PDF as vision fallback."
                    )
                    page_md_parts.append("_(scanned content; OCR unavailable — see attached original)_")
                    page_conf = 0.0
                    page_quality = "LOW"
                else:
                    _p(f"  page {idx + 1}/{page_count}: OCR running ({OCR_LANGUAGES})…")
                    any_ocr = True
                    ocr_page_count += 1
                    img = _render_page(page, dpi=OCR_DPI)
                    img, applied_rotation = _autorotate_image(img)
                    ocr_text, ocr_conf = _ocr_image(img)
                    if ocr_text.strip():
                        page_md_parts.append(ocr_text.strip())
                    page_conf = max(0.0, min(1.0, ocr_conf / 100.0))
                    page_quality = _classify_ocr_quality(ocr_conf)

            applied_rotations.append(applied_rotation)
            page_confidences.append(page_conf)
            page_qualities.append(page_quality)
            pages_md.append("\n\n".join(page_md_parts))

    body = "\n\n".join(pages_md) if pages_md else "_(no extractable content)_"

    text_for_lang = _strip_tables_and_headers(body)
    languages = _detect_languages(text_for_lang)
    overall_quality = _worst_quality(page_qualities)
    overall_confidence = min(page_confidences) if page_confidences else 0.0

    fallback_needed = (
        overall_confidence < TIER_A_MIN_CONFIDENCE
        or overall_quality == "LOW"
        or any_scan
    )

    metadata = {
        "filename": filename,
        "extractor": EXTRACTOR_VERSION,
        "page_count": page_count,
        "is_scan_or_photo": any_scan,
        "language_detected": languages,
        "applied_rotation_degrees": _summarize_rotations(applied_rotations),
        "page_rotation_metadata": _summarize_rotations(metadata_rotations),
        "ocr_used": any_ocr,
        "ocr_pages": ocr_page_count,
        "ocr_quality": overall_quality,
        "extraction_confidence": round(overall_confidence, 3),
        "fallback_attached": fallback_needed,
    }

    markdown = _assemble_markdown(filename, metadata, body)
    ocr_summary = (
        f"OCR'd {ocr_page_count}/{page_count} page(s)"
        if any_ocr
        else f"no OCR needed ({page_count} page(s) born-digital)"
    )
    _p(
        f"  {ocr_summary} · confidence={overall_confidence:.2f} "
        f"quality={overall_quality} fallback={'yes' if fallback_needed else 'no'}"
    )

    return ExtractedDocument(
        filename=filename,
        kind="pdf",
        markdown=markdown,
        metadata=metadata,
        confidence=overall_confidence,
        fallback_needed=fallback_needed,
        original_bytes=file_bytes,
        media_type="application/pdf",
        warnings=warnings,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Image path (JPG/PNG)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_image(
    filename: str,
    file_bytes: bytes,
    ext: str,
    progress_cb: Optional[Callable[[str], None]],
) -> ExtractedDocument:
    from PIL import Image

    _p = progress_cb or (lambda _msg: None)

    warnings: list[str] = []
    media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    img = Image.open(io.BytesIO(file_bytes))

    skewed = False
    applied_rotation = 0
    ocr_text = ""
    ocr_conf = 0.0
    ocr_quality = "LOW"

    ocr_ran = False
    if _resolve_tesseract():
        _p(f"  OCR running ({OCR_LANGUAGES})…")
        img, applied_rotation = _autorotate_image(img)
        ocr_text, ocr_conf = _ocr_image(img)
        ocr_quality = _classify_ocr_quality(ocr_conf)
        ocr_ran = True
        if applied_rotation == 0 and ocr_conf < OCR_MEDIUM:
            # OSD returned upright but OCR confidence is poor → likely skew/glare
            skewed = True
    else:
        warnings.append(
            "OCR unavailable; skipped. Attaching original image as vision fallback."
        )

    languages = _detect_languages(ocr_text) if ocr_text.strip() else []
    confidence = max(0.0, min(1.0, ocr_conf / 100.0))
    if skewed:
        confidence = min(confidence, 0.5)

    fallback_needed = True  # Always include original for photos (stamps, signatures, glare)

    body_lines = ["## Page 1"]
    if ocr_text.strip():
        body_lines.append(ocr_text.strip())
    else:
        body_lines.append("_(image; see attached original)_")
    if skewed:
        body_lines.append("_Note: image appears skewed or low-quality — refer to the attached original for verification._")

    metadata = {
        "filename": filename,
        "extractor": EXTRACTOR_VERSION,
        "page_count": 1,
        "is_scan_or_photo": True,
        "language_detected": languages,
        "applied_rotation_degrees": applied_rotation,
        "page_rotation_metadata": 0,
        "ocr_used": ocr_ran,
        "ocr_pages": 1 if ocr_ran else 0,
        "ocr_quality": ocr_quality,
        "extraction_confidence": round(confidence, 3),
        "fallback_attached": fallback_needed,
        "skewed": skewed,
    }

    markdown = _assemble_markdown(filename, metadata, "\n\n".join(body_lines))
    if ocr_ran:
        _p(f"  OCR'd image · confidence={confidence:.2f} quality={ocr_quality}")
    else:
        _p("  no OCR available · attaching original as fallback")

    return ExtractedDocument(
        filename=filename,
        kind="image",
        markdown=markdown,
        metadata=metadata,
        confidence=confidence,
        fallback_needed=fallback_needed,
        original_bytes=file_bytes,
        media_type=media,
        warnings=warnings,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_born_digital(page) -> bool:
    text = page.get_text("text") or ""
    word_count = len(text.split())
    if word_count < BORN_DIGITAL_MIN_WORDS:
        return False
    try:
        has_fonts = len(page.get_fonts()) > 0
    except Exception:
        has_fonts = True
    if not has_fonts:
        return False

    images = page.get_images(full=True) or []
    if not images:
        return True
    page_area = float(page.rect.width * page.rect.height) or 1.0
    image_area = 0.0
    for img_info in images:
        xref = img_info[0]
        try:
            for rect in page.get_image_rects(xref):
                image_area += float(rect.width * rect.height)
        except Exception:
            continue
    return (image_area / page_area) < IMAGE_AREA_FRACTION_THRESHOLD


def _extract_text_in_reading_order(page) -> str:
    """Use block-level extraction sorted by (y, x) so multi-column reading order is sane."""
    blocks = page.get_text("blocks") or []
    text_blocks = [b for b in blocks if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()]
    text_blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
    return "\n\n".join(b[4].rstrip() for b in text_blocks)


def _extract_tables_pdf(page, file_bytes: bytes, page_index: int) -> str:
    """Try PyMuPDF first; fall back to pdfplumber for ruled tables it misses."""
    md_tables: list[str] = []

    try:
        finder = page.find_tables()
        for tbl in finder.tables:
            score = getattr(tbl, "score", None)
            if score is not None and score < 80:
                continue
            try:
                rows = tbl.extract()
            except Exception:
                continue
            md = _rows_to_markdown(rows)
            if md:
                md_tables.append(md)
    except Exception as e:
        logger.debug("PyMuPDF find_tables failed on page %d: %s", page_index + 1, e)

    if md_tables:
        return "\n\n".join(md_tables)

    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if page_index < len(pdf.pages):
                for rows in pdf.pages[page_index].extract_tables() or []:
                    md = _rows_to_markdown(rows)
                    if md:
                        md_tables.append(md)
    except Exception as e:
        logger.debug("pdfplumber fallback failed on page %d: %s", page_index + 1, e)

    return "\n\n".join(md_tables)


def _rows_to_markdown(rows: list[list]) -> str:
    if not rows:
        return ""
    cleaned = []
    for r in rows:
        cleaned.append([_clean_cell(c) for c in r])
    if not cleaned or not any(any(c for c in r) for r in cleaned):
        return ""

    header, *body = cleaned
    if not any(h for h in header):
        header = [f"col_{i+1}" for i in range(len(cleaned[0]))]
        body = cleaned
    else:
        body = body or []

    width = max(len(header), max((len(r) for r in body), default=0))
    header = (header + [""] * width)[:width]
    body = [(r + [""] * width)[:width] for r in body]

    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _clean_cell(cell) -> str:
    if cell is None:
        return ""
    s = str(cell).replace("\r", " ").replace("\n", " ").replace("|", "/").strip()
    return re.sub(r"\s+", " ", s)


def _render_page(page, dpi: int):
    from PIL import Image

    zoom = dpi / 72.0
    import fitz

    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    mode = "RGB" if pix.n < 4 else "RGBA"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _autorotate_image(img):
    """Use Tesseract OSD to detect orientation. Returns (rotated_img, applied_degrees_cw)."""
    import pytesseract

    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0))
        conf = float(osd.get("orientation_conf", 0.0))
    except Exception as e:
        logger.debug("OSD failed: %s", e)
        return img, 0

    if rotate in (90, 180, 270) and conf > 2.0:
        # OSD returns rotation needed to bring text upright (counter-clockwise in PIL terms)
        rotated = img.rotate(-rotate, expand=True)
        return rotated, rotate
    return img, 0


def _ocr_image(img) -> tuple[str, float]:
    """Run OCR with eng+spa. Returns (text, mean confidence 0..100)."""
    import pytesseract

    try:
        data = pytesseract.image_to_data(
            img, lang=OCR_LANGUAGES, output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        logger.warning("OCR failed: %s", e)
        return "", 0.0

    words = data.get("text") or []
    confs = data.get("conf") or []
    line_nums = data.get("line_num") or []
    block_nums = data.get("block_num") or []
    par_nums = data.get("par_num") or []

    total_weight = 0
    weighted_conf = 0.0
    lines: dict[tuple, list[str]] = {}

    for i, word in enumerate(words):
        if not word or not word.strip():
            continue
        try:
            c = float(confs[i])
        except (ValueError, IndexError):
            c = -1
        if c < 0:
            continue
        key = (block_nums[i], par_nums[i], line_nums[i])
        lines.setdefault(key, []).append(word)
        w = max(1, len(word))
        total_weight += w
        weighted_conf += c * w

    if total_weight == 0:
        return "", 0.0

    sorted_keys = sorted(lines.keys())
    text_lines: list[str] = []
    last_block = None
    for k in sorted_keys:
        block = k[0]
        if last_block is not None and block != last_block:
            text_lines.append("")
        text_lines.append(" ".join(lines[k]))
        last_block = block

    return "\n".join(text_lines), weighted_conf / total_weight


def _classify_ocr_quality(mean_conf: float) -> str:
    if mean_conf >= OCR_HIGH:
        return "HIGH"
    if mean_conf >= OCR_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _worst_quality(qualities: list[str]) -> str:
    order = ["HIGH", "MEDIUM", "LOW"]
    worst = "HIGH"
    for q in qualities:
        if q in order and order.index(q) > order.index(worst):
            worst = q
    return worst


def _summarize_rotations(rotations: list[int]) -> int:
    """Return the dominant non-zero rotation, or 0 if all pages are upright."""
    non_zero = [r for r in rotations if r]
    if not non_zero:
        return 0
    return max(set(non_zero), key=non_zero.count)


def _strip_tables_and_headers(body: str) -> str:
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("|") or s.startswith("##") or s.startswith("_("):
            continue
        lines.append(line)
    return "\n".join(lines)


def _detect_languages(text: str) -> list[str]:
    if not text or len(text.split()) < 5:
        return []
    try:
        from langdetect import detect_langs, DetectorFactory
        DetectorFactory.seed = 0
        langs = detect_langs(text)
        out = []
        for lang in langs:
            if lang.prob >= 0.25 and lang.lang in ("en", "es"):
                out.append(lang.lang)
        if not out:
            top = max(langs, key=lambda l: l.prob)
            out = [top.lang]
        return out
    except Exception as e:
        logger.debug("langdetect failed: %s", e)
        return []


def _assemble_markdown(filename: str, metadata: dict, body: str) -> str:
    import json

    header_json = json.dumps(metadata, ensure_ascii=False, indent=2)
    return (
        f"```json\n{header_json}\n```\n\n"
        f"=== FILE: {filename} ===\n\n"
        f"{body}\n"
    )

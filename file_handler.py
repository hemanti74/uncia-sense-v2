import base64
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

PDF_EXTS = {".pdf"}
IMG_EXTS = {".jpg", ".jpeg", ".png"}
XML_EXTS = {".xml"}
ALLOWED_EXTS = PDF_EXTS | IMG_EXTS | XML_EXTS

if TYPE_CHECKING:
    from pdf_processor import ExtractedDocument


def build_content_blocks(
    filename: str,
    file_bytes: bytes,
    preprocess: bool = True,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> tuple[list[dict], Optional["ExtractedDocument"]]:
    """Return (content_blocks, extracted_document_or_none) for a single uploaded file.

    When `preprocess` is True, PDFs and images are extracted locally to Markdown
    via `pdf_processor`. The Markdown is sent as a text block; the original PDF
    or image is appended as a vision-fallback block only when extraction
    confidence is below the Tier A threshold. The ExtractedDocument is returned
    so callers can surface the artifact (e.g. for UI download).

    When `preprocess` is False (or the file is XML/unsupported for preprocessing),
    the original behavior is used: PDFs go as document blocks, images as image
    blocks (base64), XML inline as text — and the second tuple element is None.
    """
    ext = Path(filename).suffix.lower()

    if ext in XML_EXTS:
        text = file_bytes.decode("utf-8", errors="replace")
        return [{"type": "text", "text": f"=== FILE: {filename} ===\n{text}"}], None

    if preprocess and ext in (PDF_EXTS | IMG_EXTS):
        from pdf_processor import extract_document

        if progress_cb:
            progress_cb(f"  preprocessing {filename}…")
        try:
            doc = extract_document(filename, file_bytes, progress_cb=progress_cb)
        except Exception as e:
            if progress_cb:
                progress_cb(f"  preprocessing failed ({e}); sending original to Claude")
            return [_legacy_block(filename, file_bytes, ext)], None

        for w in doc.warnings:
            if progress_cb:
                progress_cb(f"  WARN: {w}")

        blocks: list[dict] = [{"type": "text", "text": doc.markdown}]
        if doc.fallback_needed:
            blocks.append(_legacy_block(filename, file_bytes, ext))
        return blocks, doc

    if ext in (PDF_EXTS | IMG_EXTS):
        return [_legacy_block(filename, file_bytes, ext)], None

    raise ValueError(f"Unsupported file type: {ext}")


def _legacy_block(filename: str, file_bytes: bytes, ext: str) -> dict:
    if ext in PDF_EXTS:
        data = base64.standard_b64encode(file_bytes).decode("utf-8")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            "title": filename,
        }
    media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    data = base64.standard_b64encode(file_bytes).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": data},
    }


def build_content_block(filename: str, file_bytes: bytes) -> dict:
    """Backwards-compatible single-block helper (no preprocessing). Returns
    the first block from build_content_blocks for callers that still expect a
    single dict (e.g. tests, ad-hoc scripts)."""
    blocks, _ = build_content_blocks(filename, file_bytes, preprocess=False)
    return blocks[0]

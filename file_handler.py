import base64
from pathlib import Path

PDF_EXTS = {".pdf"}
IMG_EXTS = {".jpg", ".jpeg", ".png"}
XML_EXTS = {".xml"}
ALLOWED_EXTS = PDF_EXTS | IMG_EXTS | XML_EXTS


def build_content_block(filename: str, file_bytes: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext in PDF_EXTS:
        data = base64.standard_b64encode(file_bytes).decode("utf-8")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            "title": filename,
        }
    if ext in IMG_EXTS:
        media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        data = base64.standard_b64encode(file_bytes).decode("utf-8")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": data},
        }
    if ext in XML_EXTS:
        text = file_bytes.decode("utf-8", errors="replace")
        return {
            "type": "text",
            "text": f"=== FILE: {filename} ===\n{text}",
        }
    raise ValueError(f"Unsupported file type: {ext}")

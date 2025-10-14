from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from ..models import DocumentRecord
from ..utils import compute_sha256_from_bytes, safe_filename
from ..storage import write_binary, append_record

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

try:
    import docx  # python-docx
except Exception:  # pragma: no cover
    docx = None  # type: ignore


class ExtractionResult(BaseModel):
    text: str
    excerpt: str


def _read_text_from_pdf(path: Path) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(str(path))
        pages_text = []
        for page in reader.pages:
            try:
                pages_text.append(page.extract_text() or "")
            except Exception:
                pages_text.append("")
        return "\n".join(pages_text).strip()
    except Exception:
        return ""


def _read_text_from_docx(path: Path) -> str:
    if docx is None:
        return ""
    try:
        document = docx.Document(str(path))
        text_runs = [p.text for p in document.paragraphs]
        return "\n".join(text_runs).strip()
    except Exception:
        return ""


def _read_text_from_txt(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def extract_text(path: Path, *, content_type: Optional[str]) -> ExtractionResult:
    suffix = path.suffix.lower()
    text = ""
    if suffix == ".pdf" or (content_type and "pdf" in content_type):
        text = _read_text_from_pdf(path)
    elif suffix in {".docx", ".doc"} or (content_type and "word" in content_type):
        text = _read_text_from_docx(path)
    elif suffix in {".txt", ".md"} or (content_type and "text" in content_type):
        text = _read_text_from_txt(path)
    else:
        text = _read_text_from_txt(path)

    excerpt = text[:1000]
    return ExtractionResult(text=text, excerpt=excerpt)


def ingest_bytes(
    *,
    content: bytes,
    original_filename: str,
    source: str,
    content_type: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> DocumentRecord:
    metadata = metadata or {}
    safe_name = safe_filename(original_filename or "upload.bin")
    file_path = write_binary(content, filename=safe_name)

    extraction = extract_text(file_path, content_type=content_type)

    document_id = compute_sha256_from_bytes(content)

    record = DocumentRecord(
        document_id=document_id,
        source=source,
        original_filename=original_filename or safe_name,
        content_type=content_type,
        file_path=str(file_path),
        file_size_bytes=len(content),
        text_char_count=len(extraction.text),
        text_excerpt=extraction.excerpt,
        metadata=metadata,
    )

    append_record(record)
    return record

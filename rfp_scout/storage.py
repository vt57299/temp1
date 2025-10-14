from __future__ import annotations

from pathlib import Path
from typing import Optional
import json
from datetime import datetime

from .models import DocumentRecord


DATA_DIR = Path("/workspace/data").resolve()
BIN_DIR = DATA_DIR / "bin"
INDEX_JSON = DATA_DIR / "index.jsonl"


def ensure_storage() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_JSON.exists():
        INDEX_JSON.touch()


def write_binary(content: bytes, *, filename: str) -> Path:
    ensure_storage()
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    safe_name = filename
    path = BIN_DIR / f"{timestamp}__{safe_name}"
    path.write_bytes(content)
    return path


def append_record(record: DocumentRecord) -> None:
    ensure_storage()
    with INDEX_JSON.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json())
        f.write("\n")


def get_record(document_id: str) -> Optional[DocumentRecord]:
    if not INDEX_JSON.exists():
        return None
    with INDEX_JSON.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("document_id") == document_id:
                    return DocumentRecord.model_validate(data)
            except json.JSONDecodeError:
                continue
    return None
